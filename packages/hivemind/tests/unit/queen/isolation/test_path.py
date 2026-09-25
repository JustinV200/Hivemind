"""Tests for the one isolation path: what isolating a Cell does, in order, and what it refuses.

Roadmap step 10.6a (ADR-0035). The Queen isolating a Virtual Cell writes the BLOCK wax, revokes
the Warden's grants (telling the Warden), sends every placed task Clustering's checkpoint-and-pause
pair, moves the task to PAUSED, cuts the Cell's egress on the fake backend, records `cell.isolated`
citing the report and its evidence, taints the Cell's memory from the first cited event (a
tainted Handoff from the isolated Cell is then refused by the loader every resume goes through)
and raises a CRITICAL SECURITY Alarm at the human. The Queen may never isolate the Hive Stand's
own lease: the `isolation` point refuses her there and nothing else changes; the human may. A
Night Veil Cell's isolation lives in its segment alone (codingrules 12): no wax, no label on the
Hive's tables and no record of it reaches the durable trail, and a second request reads it back
there and is answered as already isolated.

Fits into the Hive:
    Mirrors src/hivemind/queen/isolation/path.py and authority.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.isolation for the one path.
"""

from __future__ import annotations

import pytest
from builders.forage import make_grant
from builders.isolation import (
    hive_stand_cell,
    isolation_site,
    make_guard_report,
    night_veil_cell,
    place_running,
    queen_order,
    tracked_virtual_cell,
    veil_cell,
)
from builders.memory import make_handoff
from builders.queen import make_queen_deps

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.forage.grant_state import GrantState
from hivemind.hive import EgressOutcome
from hivemind.memory import MemoryContext, TaintedMemoryError, read_handoff, write_checkpoint
from hivemind.memory.cell_wax import WaxSeverity, WaxState
from hivemind.pheromone import TrailQuery
from hivemind.queen.errors import UnknownCellError
from hivemind.queen.guard_requests import GuardDeps
from hivemind.queen.isolation import (
    ISOLATED_KIND,
    IsolationRefusal,
    IsolationState,
    Isolator,
    isolate_cell,
    read_isolation,
)
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id
from waggle.messages.supervision import InterventionAction

_NO_WAIT = GuardDeps(pause_timeout_s=0.0)  # A FakeClock never advances by itself: never wait.


async def test_the_queen_isolates_a_virtual_cell_through_every_step() -> None:
    clock = FakeClock()
    egress, backend, cell = await tracked_virtual_cell(clock)
    guard = GuardDeps(egress=egress, pause_timeout_s=0.0)
    deps, link, warden_end = make_queen_deps(clock, cell=cell, guard=guard)
    task = await place_running(deps, link)
    grant = make_grant(GrantState.ACTIVE, clock, holder=link.warden_id, cell_id=cell.id)
    await deps.ledger.record_grant(grant)
    report = make_guard_report(clock, cell_id=cell.id)
    site = isolation_site(deps, link)

    outcome = await isolate_cell(site, queen_order(cell.id, report))

    assert outcome.isolated and outcome.egress is EgressOutcome.CUT
    assert backend.egress_is_cut(cell.id)  # Only its Waggle link is reachable now.
    assert outcome.revoked_grant_ids == (grant.id,) and deps.ledger.grant(grant.id) is None
    assert outcome.paused_task_ids == (task.id,)
    assert outcome.unacknowledged_task_ids == (task.id,)  # Nothing answered within no wait.
    assert (await deps.chamber.get(task.id)).status is TaskStatus.PAUSED
    await warden_end.pump_until(lambda: len(warden_end.received_kinds) >= 3)
    assert warden_end.received_kinds[:3] == ["grant_revoked", "intervene", "task_pause"]
    assert warden_end.intervenes[0].action is InterventionAction.HANDOFF
    assert warden_end.task_pauses[0].task_id == task.id
    await warden_end.close()


async def test_a_night_veil_cells_isolation_stands_in_its_segment_and_nowhere_durable() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, cell=night_veil_cell(clock), guard=_NO_WAIT)
    deps, segments = veil_cell(deps, link.cell.id)
    task = await place_running(deps, link)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=clock)
    ref = await write_checkpoint(make_handoff(clock, written_by=link.warden_id), task.id, ctx)
    site = isolation_site(deps, link)

    first = await isolate_cell(site, queen_order(link.cell.id))
    again = await isolate_cell(site, queen_order(link.cell.id))

    assert first.isolated and first.wax_id is None and first.tainted_count == 0
    assert again.already_isolated and not again.isolated  # Read back from the Cell's segment.
    assert len(await segments.query(link.cell.id, TrailQuery(kind=ISOLATED_KIND))) == 1
    durable = {event.kind for event in await deps.trail.query(TrailQuery())}
    assert not durable & {ISOLATED_KIND, "memory.tainted", "memory.wax_proposed"}
    assert await deps.memory.list_wax(link.cell.id, frozenset(WaxState), HoneyClearance.C2) == ()
    # Unlabelled: its label's record would outlive the Cell; the row goes at its teardown.
    assert (await read_handoff(deps.memory, ref, HoneyClearance.C2)).tainted is None
    await warden_end.close()


async def test_cell_isolated_cites_the_report_and_every_steps_result() -> None:
    clock = FakeClock()
    egress, _backend, cell = await tracked_virtual_cell(clock)
    deps, link, warden_end = make_queen_deps(clock, cell=cell, guard=GuardDeps(egress=egress))
    report = make_guard_report(clock, cell_id=cell.id)
    site = isolation_site(deps, link)

    outcome = await isolate_cell(site, queen_order(cell.id, report))

    [event] = await deps.trail.query(TrailQuery(kind=ISOLATED_KIND, subject_id=cell.id))
    assert event.id == outcome.event_id
    assert event.payload["report_id"] == report.id
    assert event.payload["evidence"] == list(report.event_ids)
    assert event.payload["ordered_by"] == Isolator.QUEEN.value
    assert event.payload["egress"] == EgressOutcome.CUT.value
    [wax] = await deps.memory.list_wax(cell.id, frozenset({WaxState.WRITTEN}), HoneyClearance.C2)
    assert (wax.id, wax.severity) == (event.payload["wax_id"], WaxSeverity.BLOCK)
    assert (await read_isolation(deps, cell.id)).state is IsolationState.ISOLATED
    [alarm] = site.human_inbox.alarms
    assert (alarm.kind, alarm.severity) == (AlarmKind.SECURITY, AlarmSeverity.CRITICAL)
    assert (alarm.context.cell_id, alarm.context.event_id) == (cell.id, event.id)
    assert report.id in alarm.detail
    await warden_end.close()


async def test_a_tainted_handoff_from_an_isolated_cell_is_refused() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=_NO_WAIT)
    task = await place_running(deps, link)
    suspect = new_event_id(clock)  # The first event the Guard report cites.
    clock.advance(5.0)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=clock)
    handoff = make_handoff(clock, written_by=link.warden_id, task_id=task.id)
    ref = await write_checkpoint(handoff, task.id, ctx)  # Written after the suspect event.
    report = make_guard_report(clock, cell_id=link.cell.id, event_ids=(suspect,))

    outcome = await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id, report))

    assert outcome.tainted_count >= 1
    with pytest.raises(TaintedMemoryError):
        await read_handoff(deps.memory, ref, HoneyClearance.C2)
    tainted = await deps.trail.query(TrailQuery(kind="memory.tainted"))
    assert {event.payload["source"] for event in tainted} == {"isolation"}
    assert {event.payload["cause_event_id"] for event in tainted} == {outcome.event_id}
    await warden_end.close()


async def test_an_isolation_that_stands_is_answered_as_such_and_changes_nothing() -> None:
    deps, link, warden_end = make_queen_deps(guard=_NO_WAIT)
    site = isolation_site(deps, link)
    first = await isolate_cell(site, queen_order(link.cell.id))

    again = await isolate_cell(site, queen_order(link.cell.id))

    assert first.isolated and again.already_isolated and not again.isolated
    assert len(await deps.trail.query(TrailQuery(kind=ISOLATED_KIND))) == 1
    await warden_end.close()


async def test_the_queen_is_refused_the_hive_stand_and_nothing_else_changes() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, cell=hive_stand_cell(clock), guard=_NO_WAIT)
    task = await place_running(deps, link)

    outcome = await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id))

    assert outcome.refusal is IsolationRefusal.HIVE_STAND and not outcome.isolated
    [denied] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert (denied.payload["point"], denied.payload["rule"]) == (
        "isolation",
        "guard.scope.hive_stand",
    )
    assert await deps.trail.query(TrailQuery(kind=ISOLATED_KIND)) == ()
    assert (await deps.chamber.get(task.id)).status is TaskStatus.RUNNING
    await warden_end.close()


async def test_only_the_human_isolates_the_hive_stand() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, cell=hive_stand_cell(clock), guard=_NO_WAIT)

    order = queen_order(link.cell.id, ordered_by=Isolator.HUMAN)
    outcome = await isolate_cell(isolation_site(deps, link), order)

    assert outcome.isolated and outcome.egress is EgressOutcome.UNTRACKED  # Real: left as found.
    assert await deps.trail.query(TrailQuery(kind="guard.denied")) == ()
    await warden_end.close()


async def test_a_cell_no_attached_warden_runs_is_out_of_reach() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=_NO_WAIT)

    with pytest.raises(UnknownCellError):
        await isolate_cell(isolation_site(deps, link), queen_order(new_cell_id(clock)))
    await warden_end.close()
