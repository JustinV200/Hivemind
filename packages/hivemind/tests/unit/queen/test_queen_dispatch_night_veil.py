"""Tests for the Queen's dispatcher under Night Veil and tier binding (roadmap steps 10.3a-c).

A Night Veil task is placed only when a human's own durable goal request named the tier, only on
a fresh Virtual Cell, only when this Hive configured the hidden service and the Tor proxy its
Cell will dial, and only with local model bindings; every refusal is final and loud. A task is
bound to its Cell's tier with its assignment, and re-bound when it moves.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ (codingrules section 3); split by feature from
    test_queen_dispatch_virtual.py (codingrules 14.2). Drives `dispatch_ready` directly over a
    fake `VirtualCellProvider`, with each task written to the Brood Chamber as its plan would be.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.night_veil for the placement check under test.
    - hivemind.guard.policy.floors for the floors that refuse.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from builders.cells import make_cell
from builders.forage import make_capacity
from builders.human import make_goal_request
from builders.queen import land_provisions, make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskDraft, TaskGraphDraft, TaskStatus
from hivemind.cell import CellKind, CombShieldLevel, Isolation, RequestOrigin, TaskNeeds
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.intake import receive
from hivemind.queen.placement import (
    NightVeilConstraints,
    Placement,
    PlacementPolicy,
    VirtualBackendCandidate,
)
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_warden_id
from waggle.transport.memory import MemoryTransport

_PROFILE = NightVeilConstraints(
    required_network_policy=NetworkPolicy.VPN_TOR,
    hive_stand_onion_address="7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion:8710",
    socks_proxy_url="socks5h://127.0.0.1:9050",
    locale_profile="C.UTF-8",
)
_NIGHT_VEIL_NEEDS = TaskNeeds(comb_shield=CombShieldLevel.NIGHT_VEIL, isolation=Isolation.REQUIRED)


@dataclass
class _Provider:
    """A VirtualCellProvider that hands out one pre-built link and records every placement."""

    result: WardenLink
    calls: list[Placement] = field(default_factory=list)

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Record the attempt and return `result`."""
        del task  # Not read: the placement is what a test asserts on.
        self.calls.append(placement)
        return self.result


def _link(deps: QueenDeps, tier: CombShieldLevel) -> WardenLink:
    """A freshly provisioned Virtual Cell's Warden link, at `tier`."""
    cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock, comb_shield=tier)
    warden_id = new_warden_id(deps.clock)
    hop = Hop(sender=deps.identity.hive_id, recipient=warden_id, node_id=deps.identity.node_id)
    transport, _warden_side = MemoryTransport.pair(Codec(), Codec())
    return WardenLink(warden_id=warden_id, cell=cell, transport=transport, hop=hop)


def _backend(deps: QueenDeps) -> VirtualBackendCandidate:
    """One Virtual backend with room, listing one ordinary spec (placement re-stamps the tier)."""
    spec = VirtualCellSpec(
        image="night-veil-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.EGRESS_ONLY,
        capacity=make_capacity(),
        hive_id=deps.identity.hive_id,
    )
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True)
    return VirtualBackendCandidate(name="fake", capabilities=capabilities, specs=(spec,))


def _deps(
    tier: CombShieldLevel = CombShieldLevel.NIGHT_VEIL, **overrides: object
) -> tuple[QueenDeps, _Provider]:
    """A Queen whose Virtual side provisions a Cell at `tier`, with the Night Veil profile set."""
    base, _real_link, _end = make_queen_deps()
    provider = _Provider(result=_link(base, tier))
    fields: dict[str, object] = {
        "virtual_provider": provider,
        "virtual_backends": (_backend(base),),
        "placement_policy": PlacementPolicy(prefer="virtual", night_veil=_PROFILE),
        "in_process_providers": frozenset({"fake"}),
    }
    fields.update(overrides)
    return dataclasses.replace(base, **fields), provider  # type: ignore[arg-type]


async def _submit(deps: QueenDeps, needs: TaskNeeds, request_tier: CombShieldLevel | None) -> Task:
    """Write a one-task goal to the chamber, citing a human request for `request_tier`, if any."""
    request_id = None
    if request_tier is not None:
        request = make_goal_request(deps.clock, comb_shield=request_tier)
        request_id = (await receive(deps, request)).id
    draft = make_graph_draft({"root": ()}).tasks[0]
    task = TaskDraft.model_validate(
        {
            **draft.model_dump(),
            "needs": needs,
            "origin": RequestOrigin.HUMAN,
            "goal_request_id": request_id,
        }
    )
    [minted] = await deps.chamber.submit(TaskGraphDraft(tasks=(task,)))
    return minted


async def _events(deps: QueenDeps, kind: str) -> list[PheromoneEvent]:
    """Every trail event of `kind`, oldest first."""
    return list(await deps.trail.query(TrailQuery(kind=kind)))


async def test_a_human_night_veil_request_is_placed_bound_and_cites_its_request() -> None:
    deps, provider = _deps()
    task = await _submit(deps, _NIGHT_VEIL_NEEDS, CombShieldLevel.NIGHT_VEIL)

    await dispatch_ready(deps, ())
    await land_provisions(deps, ())  # The Cell lands beside the tick; the next pass places it.

    placed = await deps.chamber.get(task.id)
    assert placed.status is TaskStatus.RUNNING
    assert placed.bound_tier is CombShieldLevel.NIGHT_VEIL
    assert len(provider.calls) == 1
    [queen_placed] = await _events(deps, "queen.placed")
    assert queen_placed.payload["goal_request_id"] == task.spec.goal_request_id
    assert await _events(deps, "guard.denied") == []


async def test_a_night_veil_tier_no_human_request_named_is_refused_and_cancelled() -> None:
    # A planner that set NIGHT_VEIL on its own: the task cites no durable request at all.
    deps, provider = _deps()
    task = await _submit(deps, _NIGHT_VEIL_NEEDS, None)

    await dispatch_ready(deps, ())

    assert (await deps.chamber.get(task.id)).status is TaskStatus.CANCELLED
    assert provider.calls == []
    [denied] = await _events(deps, "guard.denied")
    assert denied.payload["rule"] == "guard.tier_floor.night_veil_initiation"
    assert denied.payload["point"] == "placement"


async def test_a_request_that_named_no_tier_never_initiates_night_veil() -> None:
    deps, provider = _deps()
    task = await _submit(deps, _NIGHT_VEIL_NEEDS, CombShieldLevel.MEADOW)

    await dispatch_ready(deps, ())

    assert (await deps.chamber.get(task.id)).status is TaskStatus.CANCELLED
    assert provider.calls == []
    [denied] = await _events(deps, "guard.denied")
    assert "asked for MEADOW" in str(denied.payload["reason"])


async def test_an_unconfigured_night_veil_profile_is_refused_loudly_and_once() -> None:
    deps, provider = _deps(placement_policy=PlacementPolicy(prefer="virtual"))
    task = await _submit(deps, _NIGHT_VEIL_NEEDS, CombShieldLevel.NIGHT_VEIL)

    await dispatch_ready(deps, ())
    await dispatch_ready(deps, ())  # A later pass finds nothing ready: no second refusal.

    cancelled = await deps.chamber.get(task.id)
    assert cancelled.status is TaskStatus.CANCELLED
    assert provider.calls == []
    [decided] = await _events(deps, "queen.decided")
    assert "Night Veil tier profile" in str(decided.payload["detail"])


async def test_a_profile_with_no_tor_proxy_is_refused_by_the_control_link_floor() -> None:
    profile = dataclasses.replace(_PROFILE, socks_proxy_url="")
    policy = PlacementPolicy(prefer="virtual", night_veil=profile)
    deps, provider = _deps(placement_policy=policy)
    task = await _submit(deps, _NIGHT_VEIL_NEEDS, CombShieldLevel.NIGHT_VEIL)

    await dispatch_ready(deps, ())

    assert (await deps.chamber.get(task.id)).status is TaskStatus.CANCELLED
    assert provider.calls == []
    [denied] = await _events(deps, "guard.denied")
    assert denied.payload["rule"] == "guard.tier_floor.night_veil_control_link"


async def test_a_night_veil_grant_keeps_no_binding_that_is_not_local() -> None:
    # The Forage map's one source is the in-process `fake`, but this Queen was told of none.
    deps, _provider = _deps(in_process_providers=frozenset())
    task = await _submit(deps, _NIGHT_VEIL_NEEDS, CombShieldLevel.NIGHT_VEIL)

    await dispatch_ready(deps, ())
    await land_provisions(deps, ())  # The Cell lands beside the tick; the next pass places it.

    assert (await deps.chamber.get(task.id)).status is TaskStatus.FAILED
    rules = {event.payload["rule"] for event in await _events(deps, "guard.denied")}
    assert rules == {"guard.tier_floor.night_veil_local_slots"}
    assert len(await _events(deps, "forage.denied")) == 1


async def test_a_task_moved_to_a_cell_of_another_tier_is_re_bound_before_it_runs() -> None:
    deps, _provider = _deps(CombShieldLevel.PROPOLIS)
    task = await _submit(deps, TaskNeeds(), None)
    lost = _link(deps, CombShieldLevel.MEADOW)
    await deps.chamber.assign(
        task.id, lost.warden_id, lost.cell.id, "First placement.", bound_tier=CombShieldLevel.MEADOW
    )
    await deps.chamber.unassign(task.id, "Its Warden was lost before the bee started.")

    await dispatch_ready(deps, ())
    await land_provisions(deps, ())  # The Cell lands beside the tick; the next pass places it.

    moved = await deps.chamber.get(task.id)
    assert moved.status is TaskStatus.RUNNING
    assert moved.bound_tier is CombShieldLevel.PROPOLIS
    trail = [
        (event.kind, event.payload.get("bound_tier"))
        for event in await deps.trail.query(TrailQuery(subject_id=task.id))
        if event.kind.startswith("task.")
    ]
    assert trail[-4:] == [
        ("task.assigned", "MEADOW"),
        ("task.unassigned", None),
        ("task.assigned", "PROPOLIS"),
        ("task.started", None),
    ]
