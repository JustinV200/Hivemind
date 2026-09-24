"""Tests for the human's lift: placement and egress come back, tainted memory stays tainted.

Roadmap step 10.6a (ADR-0035). While a Cell is isolated, placement refuses it outright (its BLOCK
wax); the human's lift clears that wax, gives a Virtual Cell its egress back, records
`cell.isolation_lifted` naming the isolation it ended, and placement lands there again. What the
isolation tainted stays tainted and what it paused stays paused. A lift also releases the
Queen's placement holds on a Cell (the Hive Stand's fallback), so a held goal is placed there
again; a lift with nothing to lift changes nothing and raises.

Fits into the Hive:
    Mirrors src/hivemind/queen/isolation/lift.py, and the Guard holds in src/hivemind/queen/
    dispatcher/snapshot.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.isolation.lift for the lift.
"""

from __future__ import annotations

import pytest
from builders.isolation import (
    hive_stand_cell,
    isolation_site,
    make_decision,
    make_guard_request,
    make_hold,
    place_running,
    queen_order,
    tracked_virtual_cell,
)
from builders.memory import make_handoff
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.hive import EgressOutcome
from hivemind.memory import MemoryContext, TaintedMemoryError, read_handoff, write_checkpoint
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.snapshot import build_forage_view, build_inventory
from hivemind.queen.errors import CellNotIsolatedError
from hivemind.queen.guard_requests import GuardDeps
from hivemind.queen.isolation import (
    LIFTED_KIND,
    IsolationState,
    isolate_cell,
    lift_isolation,
    read_isolation,
)
from hivemind.queen.placement import Placement, PlacementError, ReuseReal, decide
from waggle.clock import FakeClock
from waggle.ids import new_device_id


async def test_a_lift_restores_placement_and_egress_but_not_trust() -> None:
    clock = FakeClock()
    egress, backend, cell = await tracked_virtual_cell(clock)
    deps, link, warden_end = make_queen_deps(
        clock, cell=cell, guard=GuardDeps(egress=egress, pause_timeout_s=0.0)
    )
    paused = await place_running(deps, link)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=clock)
    ref = await write_checkpoint(make_handoff(clock, written_by=link.warden_id), paused.id, ctx)
    site = isolation_site(deps, link)
    isolated = await isolate_cell(site, queen_order(cell.id))
    with pytest.raises(PlacementError):  # Nothing is placed on an isolated Cell.
        await _placement(deps, link, paused)

    lifted = await lift_isolation(site, cell.id, new_device_id(clock))

    assert lifted.isolated_event_id == isolated.event_id
    assert lifted.wax_cleared == isolated.wax_id and lifted.egress is EgressOutcome.RESTORED
    assert not backend.egress_is_cut(cell.id)
    assert (await read_isolation(deps, cell.id)).state is IsolationState.OPEN
    placement = await _placement(deps, link, paused)
    assert isinstance(placement, ReuseReal) and placement.cell_id == cell.id
    with pytest.raises(TaintedMemoryError):  # Still tainted: only a judge clears it.
        await read_handoff(deps.memory, ref, HoneyClearance.C2)
    assert (await deps.chamber.get(paused.id)).status is TaskStatus.PAUSED
    [event] = await deps.trail.query(TrailQuery(kind=LIFTED_KIND, subject_id=cell.id))
    assert event.payload["isolated_event_id"] == isolated.event_id
    await warden_end.close()


async def test_a_lift_with_nothing_to_lift_raises_and_records_nothing() -> None:
    deps, link, warden_end = make_queen_deps(guard=GuardDeps(pause_timeout_s=0.0))

    with pytest.raises(CellNotIsolatedError):
        await lift_isolation(isolation_site(deps, link), link.cell.id, None)
    assert await deps.trail.query(TrailQuery(kind=LIFTED_KIND)) == ()
    await warden_end.close()


async def test_a_held_goal_is_kept_off_the_hive_stand_until_the_human_lifts_the_hold() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, cell=hive_stand_cell(clock))
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    request = make_guard_request(clock)
    await deps.guard.requests.file(request)
    hold = make_hold(request, clock, goal_ids=(task.goal_id,))
    hold = hold.model_copy(update={"cell_id": link.cell.id})
    await deps.guard.requests.decide(request.id, make_decision(clock), hold)
    with pytest.raises(PlacementError, match="held here by Guard report"):
        await _placement(deps, link, task)

    lifted = await lift_isolation(isolation_site(deps, link), link.cell.id, new_device_id(clock))

    assert lifted.released_holds == 1 and lifted.isolated_event_id is None
    placement = await _placement(deps, link, task)
    assert isinstance(placement, ReuseReal) and placement.cell_id == link.cell.id
    await warden_end.close()


async def test_a_hold_blocks_only_its_own_goal() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, cell=hive_stand_cell(clock))
    [held] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    [other] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    request = make_guard_request(clock)
    await deps.guard.requests.file(request)
    hold = make_hold(request, clock, goal_ids=(held.goal_id,))
    hold = hold.model_copy(update={"cell_id": link.cell.id})
    await deps.guard.requests.decide(request.id, make_decision(clock), hold)

    placement = await _placement(deps, link, other)

    assert isinstance(placement, ReuseReal) and placement.cell_id == link.cell.id
    await warden_end.close()


async def _placement(deps: QueenDeps, link: WardenLink, task: Task) -> Placement:
    """Run the dispatcher's own placement decision for `task`, with its goal's holds applied."""
    inventory = await build_inventory(deps, (link,), goal_id=task.goal_id)
    return decide(task.spec.needs, inventory, build_forage_view(deps, task), deps.placement_policy)
