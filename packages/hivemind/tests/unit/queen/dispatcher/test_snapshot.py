"""Tests for hivemind.queen.dispatcher.snapshot: build_inventory and build_forage_view.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/snapshot.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.snapshot for the module under test.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from builders.forage import make_source
from builders.queen import make_queen_deps
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import CombShieldLevel, HoneyClearance, RequestOrigin
from hivemind.forage import ForageMap, HostingPlan, SlotPlan, SourceChain
from hivemind.forage.slots import ModelSlot
from hivemind.hive import BackendCapabilities
from hivemind.hive.lifecycle import LifecycleDormantCell, LifecycleVirtualBackend
from hivemind.memory import MemoryContext, WaxSeverity
from hivemind.memory.cell_wax.writes import WaxProposalInput, propose_wax, write_wax
from hivemind.queen.deps import QueenDeps
from hivemind.queen.dispatcher.snapshot import (
    build_forage_view,
    build_inventory,
    dormant_candidate_from_lifecycle,
    virtual_backend_candidate_from_lifecycle,
)
from hivemind.queen.placement import DormantCandidate, VirtualBackendCandidate
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id
from waggle.messages.cell.wax import WaxDecision, WaxOrigin


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
    await warden_end.close()


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
