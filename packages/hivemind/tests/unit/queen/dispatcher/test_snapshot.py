"""Tests for hivemind.queen.dispatcher.snapshot: build_inventory and build_forage_view.

The dispatcher lifecycle fix's placement figures are pinned here too: an attached Cell's room is
its live capacity less the grants in force on it, and a backend's room is less every fresh Cell
still being provisioned on it (both used to read figures that never moved).

An attached Night Veil Cell is never a placement candidate: it holds only the task it was
provisioned for, isolated or not (codingrules 8.7 and 12).

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/snapshot.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.snapshot for the module under test.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence

from builders.cells import make_cell
from builders.forage import make_capacity, make_grant, make_host_capacity, make_source
from builders.queen import make_queen_deps
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import CellKind, CombShieldLevel, HoneyClearance, RequestOrigin
from hivemind.forage import (
    ForageCapacity,
    ForageGrant,
    ForageMap,
    HostingPlan,
    SlotPlan,
    SourceChain,
)
from hivemind.forage.grant_state import GrantState
from hivemind.forage.slots import ModelSlot
from hivemind.hive import BackendCapabilities, VirtualCellSpec
from hivemind.hive.lifecycle import LifecycleDormantCell, LifecycleVirtualBackend
from hivemind.memory import MemoryContext, WaxSeverity
from hivemind.memory.cell_wax.writes import WaxProposalInput, propose_wax, write_wax
from hivemind.queen.deps import ProvisionJob, QueenDeps, WardenLink
from hivemind.queen.dispatcher.snapshot import (
    build_forage_view,
    build_inventory,
    dormant_candidate_from_lifecycle,
    virtual_backend_candidate_from_lifecycle,
)
from hivemind.queen.placement import (
    DormantCandidate,
    Placement,
    ProvisionVirtual,
    VirtualBackendCandidate,
)
from waggle.clock import FakeClock
from waggle.ids import CellId, TaskId, new_cell_id, new_task_id
from waggle.messages.cell.wax import WaxDecision, WaxOrigin

_MIB = 1024**2


async def _write_wax(deps: QueenDeps, cell_id: CellId, severity: WaxSeverity, text: str) -> None:
    """Propose then write one CellWax note directly, bypassing autopilot for this test's setup."""
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    proposal = WaxProposalInput(
        cell_id=cell_id,
        severity=severity,
        text=text,
        reason="test setup",
        clearance=HoneyClearance.C1,
        origin=WaxOrigin.HUMAN,
    )
    proposed = await propose_wax(proposal, text_cap_chars=4_000, ctx=ctx)
    await write_wax(proposed, WaxDecision.AUTOPILOT, "test setup", ctx)


async def test_build_inventory_carries_one_real_candidate_per_attached_warden() -> None:
    deps, link, warden_end = make_queen_deps()

    inventory = await build_inventory(deps, (link,))

    assert len(inventory.real) == 1
    assert inventory.real[0].cell_id == link.cell.id
    assert inventory.real[0].warden_id == link.warden_id
    assert inventory.real[0].has_free_capacity is True
    # Regression (roadmap step 6.12): without the Cell's own level, placement fell back to
    # READ_ONLY and no Real Cell could ever take Exoskeleton work.
    assert inventory.real[0].access_level is link.cell.access_level
    await warden_end.close()


async def test_build_inventory_counts_the_grants_in_force_against_a_cells_cap() -> None:
    # A two-bee Cell: one task's grant (twice, a retry's too) leaves room; a second task's fills it.
    deps, link, warden_end = make_queen_deps(cell=make_cell(capacity=make_capacity(max_sub_bees=2)))
    retried = new_task_id(deps.clock)
    for _attempt in range(2):
        await deps.ledger.record_grant(_grant_on(deps, link, retried))

    one_task = await build_inventory(deps, (link,))
    await deps.ledger.record_grant(_grant_on(deps, link, new_task_id(deps.clock)))
    two_tasks = await build_inventory(deps, (link,))

    assert one_task.real[0].has_free_capacity is True
    assert two_tasks.real[0].has_free_capacity is False
    await warden_end.close()


async def test_build_inventory_reads_a_cells_capacity_live_where_its_link_can() -> None:
    # Probed with 8 GiB free when the Hive was built; read now, 256 MiB are left: no 512 MiB bee.
    deps, link, warden_end = make_queen_deps()
    starved = make_capacity(host=make_host_capacity(memory_free_bytes=256 * _MIB))

    async def read() -> ForageCapacity:
        return starved

    inventory = await build_inventory(deps, (dataclasses.replace(link, live_capacity=read),))

    assert inventory.real[0].has_free_capacity is False
    await warden_end.close()


async def test_a_fresh_cell_being_provisioned_holds_its_backends_room_until_it_ends() -> None:
    deps, link, warden_end = make_queen_deps()
    deps = dataclasses.replace(deps, virtual_backends=(_backend_with_room(deps, headroom=1),))
    placement = ProvisionVirtual(spec=_spec(deps), backend="fake", reason="test")
    landed = asyncio.Event()

    async def acquisition() -> tuple[WardenLink, Placement]:
        await landed.wait()
        return link, placement

    job = asyncio.ensure_future(acquisition())
    deps.dispatch.provisions.jobs[new_task_id(deps.clock)] = ProvisionJob(placement, job)

    in_flight = await build_inventory(deps, (link,))
    landed.set()
    await job
    ended = await build_inventory(deps, (link,))

    assert in_flight.virtual_backends[0].capabilities.headroom == 0
    # Ended: its Cell is the lifecycle's to count now (none here: this backend's room is static).
    assert ended.virtual_backends[0].capabilities.headroom == 1
    await warden_end.close()


def _grant_on(deps: QueenDeps, link: WardenLink, task_id: TaskId) -> ForageGrant:
    """A live grant on `link`'s Cell, held by its Warden, for `task_id`."""
    return make_grant(
        state=GrantState.ACTIVE,
        clock=deps.clock,
        holder=link.warden_id,
        cell_id=link.cell.id,
        task_id=task_id,
    )


def _spec(deps: QueenDeps) -> VirtualCellSpec:
    """A plain MEADOW spec for this Hive."""
    return VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        capacity=make_capacity(),
        hive_id=deps.identity.hive_id,
    )


def _backend_with_room(deps: QueenDeps, *, headroom: int) -> VirtualBackendCandidate:
    """The `fake` backend, with `headroom` Cells' room left."""
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True, headroom=headroom)
    return VirtualBackendCandidate(name="fake", capabilities=capabilities, specs=(_spec(deps),))


async def test_an_attached_night_veil_cell_is_never_a_placement_candidate() -> None:
    clock = FakeClock()
    veiled = make_cell(CellKind.VIRTUAL, clock, comb_shield=CombShieldLevel.NIGHT_VEIL)
    deps, night_veil, warden_end = make_queen_deps(clock, cell=veiled)
    _, meadow, meadow_end = make_queen_deps(clock, cell=make_cell(CellKind.VIRTUAL, clock))

    inventory = await build_inventory(deps, (night_veil, meadow))

    assert [candidate.cell_id for candidate in inventory.real] == [meadow.cell.id]
    await warden_end.close()
    await meadow_end.close()


async def test_build_inventory_reads_blocked_and_cautioned_wax() -> None:
    deps, link, warden_end = make_queen_deps()
    await _write_wax(deps, link.cell.id, WaxSeverity.BLOCK, "disk nearly full")

    inventory = await build_inventory(deps, (link,))

    assert link.cell.id in inventory.blocked
    assert inventory.blocked[link.cell.id].text == "disk nearly full"
    assert inventory.cautioned == {}
    await warden_end.close()


async def test_build_inventory_reads_deps_own_virtual_backends_and_dormant_cells() -> None:
    backend = VirtualBackendCandidate(
        name="docker", capabilities=BackendCapabilities(can_snapshot=False, can_pause=True)
    )
    deps, link, warden_end = make_queen_deps(virtual_backends=(backend,))

    inventory = await build_inventory(deps, (link,))

    assert inventory.virtual_backends == (backend,)
    await warden_end.close()


async def test_build_inventory_virtual_backends_override_replaces_deps_own_backends() -> None:
    original = VirtualBackendCandidate(
        name="docker", capabilities=BackendCapabilities(can_snapshot=False, can_pause=True)
    )
    deps, link, warden_end = make_queen_deps(virtual_backends=(original,))
    zeroed = VirtualBackendCandidate(
        name="docker",
        capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=0),
    )

    inventory = await build_inventory(deps, (link,), virtual_backends=(zeroed,))

    assert inventory.virtual_backends == (zeroed,)
    await warden_end.close()


async def test_build_inventory_exclude_dormant_drops_the_named_cell() -> None:
    dormant = DormantCandidate(
        cell_id=CellId("cell_dormant"),
        warden_id=None,
        image="base-ubuntu",
        comb_shield=CombShieldLevel.MEADOW,
    )
    deps, link, warden_end = make_queen_deps(dormant_cells=(dormant,))

    inventory = await build_inventory(deps, (link,), exclude_dormant=frozenset({dormant.cell_id}))

    assert inventory.dormant == ()
    await warden_end.close()


async def test_build_forage_view_carries_the_drone_footprint() -> None:
    deps, _link, warden_end = make_queen_deps()
    task = make_task()

    forage_view = build_forage_view(deps, task)

    assert forage_view.footprint is not None
    await warden_end.close()


async def test_build_forage_view_carries_the_task_s_own_request_origin() -> None:
    deps, _link, warden_end = make_queen_deps()
    task = make_task(spec=make_task_spec(origin=RequestOrigin.QUEEN))

    forage_view = build_forage_view(deps, task)

    assert forage_view.request_origin is RequestOrigin.QUEEN
    await warden_end.close()


async def test_build_forage_view_defaults_night_veil_hosting_with_no_cell_id() -> None:
    deps, _link, warden_end = make_queen_deps()
    task = make_task()  # PENDING: cell_id is None, no placement decided yet.

    forage_view = build_forage_view(deps, task)

    assert forage_view.night_veil_hosting.all_local is True
    assert forage_view.night_veil_hosting.non_local_slots == ()
    await warden_end.close()


async def test_build_forage_view_checks_an_existing_hosting_plan_for_task_cell_id() -> None:
    clock = FakeClock()
    deps, _link, warden_end = make_queen_deps(clock=clock)
    cell_id = new_cell_id(clock)
    local_source = make_source(source_id="local", host_cell_id=cell_id)
    hosted_source = make_source(source_id="hosted", host_cell_id=None)
    deps = dataclasses.replace(deps, map=ForageMap((local_source, hosted_source), clock))
    plan = HostingPlan(
        cell_id=cell_id,
        default=SourceChain(primary="local", fallbacks=()),
        slots=(SlotPlan(slot=ModelSlot.WORKER, chain=SourceChain(primary="hosted", fallbacks=())),),
        reason="test plan for the Night Veil hosting check",
    )
    await deps.ledger.decisions.record_plan(plan)
    task = make_task(status=TaskStatus.RUNNING, clock=clock, cell_id=cell_id)

    forage_view = build_forage_view(deps, task)

    assert forage_view.night_veil_hosting.all_local is False
    assert forage_view.night_veil_hosting.non_local_slots != ()
    await warden_end.close()


async def test_build_inventory_prefers_the_live_virtual_backend_source_over_the_static_tuple() -> (
    None
):
    stale = VirtualBackendCandidate(
        name="docker", capabilities=BackendCapabilities(can_snapshot=False, can_pause=True)
    )
    live = VirtualBackendCandidate(
        name="qemu", capabilities=BackendCapabilities(can_snapshot=False, can_pause=True)
    )
    deps, link, warden_end = make_queen_deps(virtual_backends=(stale,))
    deps = dataclasses.replace(deps, virtual_backend_source=lambda: _once((live,)))

    inventory = await build_inventory(deps, (link,))

    assert inventory.virtual_backends == (live,)
    await warden_end.close()


async def test_build_inventory_prefers_the_live_dormant_cell_source_over_the_static_tuple() -> None:
    stale = DormantCandidate(
        cell_id=CellId("cell_stale"), warden_id=None, image="x", comb_shield=CombShieldLevel.MEADOW
    )
    live = DormantCandidate(
        cell_id=CellId("cell_live"), warden_id=None, image="x", comb_shield=CombShieldLevel.MEADOW
    )
    deps, link, warden_end = make_queen_deps(dormant_cells=(stale,))
    deps = dataclasses.replace(deps, dormant_cell_source=lambda: _once((live,)))

    inventory = await build_inventory(deps, (link,))

    assert inventory.dormant == (live,)
    await warden_end.close()


async def test_virtual_backend_candidate_from_lifecycle_converts_name_and_capabilities() -> None:
    hive_backend = LifecycleVirtualBackend(
        name="qemu",
        capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=3),
    )

    candidate = virtual_backend_candidate_from_lifecycle(hive_backend)

    assert candidate.name == "qemu"
    assert candidate.capabilities.headroom == 3
    assert candidate.specs == ()


async def test_dormant_candidate_from_lifecycle_converts_every_field() -> None:
    hive_dormant = LifecycleDormantCell(
        cell_id=CellId("cell_x"),
        warden_id=None,
        image="base-ubuntu",
        comb_shield=CombShieldLevel.MEADOW,
    )

    candidate = dormant_candidate_from_lifecycle(hive_dormant)

    assert candidate.cell_id == CellId("cell_x")
    assert candidate.image == "base-ubuntu"
    assert candidate.comb_shield is CombShieldLevel.MEADOW


async def _once[T](value: Sequence[T]) -> tuple[T, ...]:
    """An awaitable that returns `value`, for a fake virtual_backend_source/dormant_cell_source."""
    return tuple(value)
