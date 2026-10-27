"""Tests for the one Sting Cut path: what cutting a Cell off does, in order, and what it refuses.

Roadmap step 13.4a. The human's cut of a Virtual Cell isolates it first, keeps a snapshot, revokes
its grants, has its Warden release the lease, forgets its key and closes its link, records
`cell.sting_cut` and raises a CRITICAL SECURITY Alarm; a second cut changes nothing. A Night
Veil Cell keeps nothing and is torn down after the record, which lives in its segment alone. A
Real Cell's Warden is ordered to release its lease. The Queen's own cut is refused, with nothing
scrubbed, on the Hive Stand, on a Cell not isolated, past her hourly cap, and where no evidence
could be kept.

Fits into the Hive:
    Mirrors src/hivemind/queen/sting_cut/path.py, steps.py and authority.py (codingrules 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.sting_cut for the one path.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.cells import make_cell
from builders.forage import make_grant
from builders.isolation import (
    hive_stand_cell,
    isolation_site,
    night_veil_cell,
    queen_order,
    veil_cell,
)
from builders.queen import make_queen_deps
from builders.sting_cut import FakeCellCut, cut_order

from hivemind.cell import Cell, CellKind
from hivemind.forage.grant_state import GrantState
from hivemind.pheromone import TrailQuery
from hivemind.queen.guard_requests import GuardDeps
from hivemind.queen.isolation import isolate_cell
from hivemind.queen.sting_cut import (
    STING_CUT_KIND,
    Cutter,
    ScrubResult,
    StingCutRefusal,
    sting_cut_cell,
)
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import FakeClock
from waggle.messages.supervision import InterventionAction


async def test_the_humans_cut_of_a_virtual_cell_runs_every_step_once() -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    cut = FakeCellCut(cell.id)
    deps, link, warden_end = make_queen_deps(
        clock, cell=cell, guard=GuardDeps(cut=cut, pause_timeout_s=0.0)
    )
    grant = make_grant(GrantState.ACTIVE, clock, holder=link.warden_id, cell_id=cell.id)
    await deps.ledger.record_grant(grant)
    site = isolation_site(deps, link)

    outcome = await sting_cut_cell(site, cut_order(cell.id))
    again = await sting_cut_cell(site, cut_order(cell.id))

    assert outcome.cut and outcome.isolated_event_id is not None  # Isolated first, by the human.
    assert outcome.snapshot_id == f"snap_{cell.id}"
    assert deps.ledger.grant(grant.id) is None
    assert (outcome.scrub, outcome.key_forgotten) == (ScrubResult.CONFIRMED, True)
    assert [step for step, _ in cut.calls] == ["keep_evidence", "scrub", "forget_key"]
    assert not link.transport.is_connected  # Its session ends with its key.
    [event] = await deps.trail.query(TrailQuery(kind=STING_CUT_KIND, subject_id=cell.id))
    assert (event.payload["cut_by"], event.payload["snapshot_id"]) == ("human", outcome.snapshot_id)
    alarm = site.human_inbox.alarms[-1]
    assert (alarm.kind, alarm.severity) == (AlarmKind.SECURITY, AlarmSeverity.CRITICAL)
    assert alarm.context.event_id == event.id
    assert again.already_cut and not again.cut
    await warden_end.close()


async def test_a_night_veil_cell_keeps_nothing_and_is_torn_down_after_the_record() -> None:
    clock = FakeClock()
    cell = night_veil_cell(clock)
    cut = FakeCellCut(cell.id)
    deps, link, warden_end = make_queen_deps(
        clock, cell=cell, guard=GuardDeps(cut=cut, pause_timeout_s=0.0)
    )
    deps, segments = veil_cell(deps, cell.id)

    outcome = await sting_cut_cell(isolation_site(deps, link), cut_order(cell.id))

    assert outcome.cut and outcome.torn_down and outcome.snapshot_id is None
    assert [step for step, _ in cut.calls] == ["scrub", "forget_key", "teardown"]
    assert len(await segments.query(cell.id, TrailQuery(kind=STING_CUT_KIND))) == 1
    durable = await deps.trail.query(TrailQuery(kind=STING_CUT_KIND))
    assert all(not event.payload for event in durable)  # The skeleton at most: no payload.
    await warden_end.close()


async def test_a_real_cells_warden_is_ordered_to_release_its_lease() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=GuardDeps(pause_timeout_s=0.0))

    outcome = await sting_cut_cell(isolation_site(deps, link), cut_order(link.cell.id))

    assert outcome.cut and outcome.scrub is ScrubResult.ORDERED and not outcome.key_forgotten
    await warden_end.pump_until(
        lambda: any(i.action is InterventionAction.RELEASE_LEASE for i in warden_end.intervenes)
    )
    await warden_end.close()


@dataclass(frozen=True)
class _QueenCase:
    """One refusal of the Queen's own cut: the Cell, her guard settings, and the refusal."""

    cell: str  # "hive_stand", "virtual" (tracked, snapshots) or "real" (no Virtual side).
    isolated: bool
    cap: int
    refusal: StingCutRefusal


@pytest.mark.parametrize(
    "case",
    [
        _QueenCase("hive_stand", isolated=False, cap=2, refusal=StingCutRefusal.HIVE_STAND),
        _QueenCase("virtual", isolated=False, cap=2, refusal=StingCutRefusal.NOT_ISOLATED),
        _QueenCase("virtual", isolated=True, cap=0, refusal=StingCutRefusal.HOURLY_CAP),
        _QueenCase("real", isolated=True, cap=2, refusal=StingCutRefusal.NO_EVIDENCE),
    ],
    ids=lambda case: case.refusal.value,
)
async def test_the_queens_cut_is_refused_with_nothing_scrubbed(case: _QueenCase) -> None:
    clock = FakeClock()
    cell = _cell(case.cell, clock)
    cut = FakeCellCut(cell.id) if case.cell == "virtual" else None
    guard = GuardDeps(cut=cut, pause_timeout_s=0.0, max_sting_cuts_per_hour=case.cap)
    deps, link, warden_end = make_queen_deps(clock, cell=cell, guard=guard)
    site = isolation_site(deps, link)
    if case.isolated:
        await isolate_cell(site, queen_order(cell.id))

    outcome = await sting_cut_cell(site, cut_order(cell.id, Cutter.QUEEN))

    assert outcome.refusal is case.refusal and not outcome.cut
    assert await deps.trail.query(TrailQuery(kind=STING_CUT_KIND)) == ()
    assert cut is None or ("scrub", cell.id) not in cut.calls
    assert link.transport.is_connected
    if case.refusal in (StingCutRefusal.HIVE_STAND, StingCutRefusal.HOURLY_CAP):
        [denied] = await deps.trail.query(TrailQuery(kind="guard.denied"))
        assert denied.payload["rule"] == f"guard.scope.{case.refusal.value}"
    await warden_end.close()


def _cell(name: str, clock: FakeClock) -> Cell:
    """The Cell a refusal case names."""
    if name == "hive_stand":
        return hive_stand_cell(clock)
    return make_cell(kind=CellKind.VIRTUAL if name == "virtual" else CellKind.REAL, clock=clock)
