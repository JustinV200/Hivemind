"""Test hivemind.observation.api.capping: proposals, verdicts, rollbacks and audit rates.

An empty trail is an empty view. A proposal walks propose, check, cap, apply and verify and ends
VERIFIED; a failed check ends REJECTED with the failing check's code; a failed postcondition ends
ROLLED_BACK with its method; every row carries its bee and task; counts are per tier and state;
audit rates are per tier (a judge that never answered is inconclusive, not a rejection); and a
Cell's narrowing keeps only its own proposals.

Fits into the Hive:
    Mirrors src/hivemind/observation/api/capping.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.observation import after, make_event
from pydantic import JsonValue

from hivemind.observation import MAX_ROWS, capping_view, cappings_on_cell
from hivemind.pheromone import PheromoneEvent
from hivemind.supervision.capping import JudgeOutcome, ProposalState
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_message_id, new_task_id, new_warden_id

_CLOCK = FakeClock()
_BEE = new_warden_id(_CLOCK)


def _proposal(
    pid: str, steps: list[tuple[str, dict[str, JsonValue]]], *, cell: str, task: str
) -> list[PheromoneEvent]:
    """A proposal's events, one second apart from second 1 on; the first is its proposal."""
    events: list[PheromoneEvent] = [
        make_event(
            "capping.proposed",
            pid,
            after(1),
            actor=_BEE,
            payload={"task_id": task, "cell_id": cell, "tier": "SCRATCH_WRITE"},
        )
    ]
    for second, (kind, payload) in enumerate(steps, start=2):
        events.append(make_event(kind, pid, after(second), actor=_BEE, payload=payload))
    return events


def test_an_empty_trail_is_an_empty_view() -> None:
    view = capping_view([])

    assert (view.in_flight, view.finished, view.by_tier_state) == ([], [], [])
    assert (view.verdicts, view.rollbacks, view.audits, view.audit_rates) == ([], [], [], [])


def _four_proposals(ids: tuple[str, str, str, str], cell: str, task: str) -> list[PheromoneEvent]:
    """Events of a verified, a rejected, a rolled-back and a still-checking proposal."""
    ok, bad, undone, waiting = ids
    checked: tuple[str, dict[str, JsonValue]] = (
        "capping.checked",
        {"check": "SCOPE", "outcome": "PASSED"},
    )
    failed: tuple[str, dict[str, JsonValue]] = (
        "capping.checked",
        {"check": "JUDGE", "outcome": "FAILED", "judge_error": True},
    )
    rejected: tuple[str, dict[str, JsonValue]] = (
        "capping.rejected",
        {"tier": "SCRATCH_WRITE", "checks_run": 1, "failing_check": "JUDGE"},
    )
    stories: list[tuple[str, list[tuple[str, dict[str, JsonValue]]]]] = [
        (
            ok,
            [
                checked,
                ("capping.capped", {"tier": "SCRATCH_WRITE", "checks_passed": 1}),
                ("capping.applied", {"action_kind": "DIFF"}),
                ("capping.verified", {"postconditions_held": 1}),
            ],
        ),
        (bad, [failed, rejected]),
        (
            undone,
            [
                ("capping.capped", {"tier": "SCRATCH_WRITE", "checks_passed": 2}),
                ("capping.applied", {"action_kind": "COMMAND"}),
                ("capping.rolled_back", {"method": "REVERSE_DIFF"}),
            ],
        ),
        (waiting, [checked]),
    ]
    return [
        event for pid, steps in stories for event in _proposal(pid, steps, cell=cell, task=task)
    ]


def test_each_proposal_ends_in_the_state_its_newest_event_reached() -> None:
    cell, task = new_cell_id(_CLOCK), new_task_id(_CLOCK)
    ok, bad, undone, waiting = (new_message_id(_CLOCK) for _ in range(4))
    events = _four_proposals((ok, bad, undone, waiting), cell, task)

    view = capping_view(list(reversed(events)))  # Arrival order does not matter.

    states = {row.id: row.state for row in [*view.in_flight, *view.finished]}
    assert states == {
        ok: ProposalState.VERIFIED,
        bad: ProposalState.REJECTED,
        undone: ProposalState.ROLLED_BACK,
        waiting: ProposalState.CHECKING,
    }
    assert [row.id for row in view.in_flight] == [waiting]
    assert [(row.id, row.rollback_method) for row in view.rollbacks] == [(undone, "REVERSE_DIFF")]
    rejected = next(row for row in view.finished if row.id == bad)
    assert (rejected.failing_check, rejected.bee_id, rejected.task_id) == ("JUDGE", _BEE, task)
    assert next(row for row in view.finished if row.id == ok).action_kind == "DIFF"


def test_a_verdict_sorted_ahead_of_its_tied_proposal_still_finishes_the_proposal() -> None:
    # Both events carry the same instant, as two records in one clock tick do on a coarse clock;
    # the trail then orders them by id, which says nothing about which came first.
    cell, task, pid = new_cell_id(_CLOCK), new_task_id(_CLOCK), new_message_id(_CLOCK)
    proposed = make_event(
        "capping.proposed",
        pid,
        after(1),
        actor=_BEE,
        payload={"task_id": task, "cell_id": cell, "tier": "READ_ONLY"},
    )
    rejected = make_event(
        "capping.rejected",
        pid,
        after(1),
        actor=_BEE,
        payload={"tier": "READ_ONLY", "failing_check": "SCOPE"},
    )

    view = capping_view([rejected, proposed])

    [row] = view.finished
    assert (row.state, row.failing_check, row.task_id, row.cell_id) == (
        ProposalState.REJECTED,
        "SCOPE",
        task,
        cell,
    )
    assert view.in_flight == []


def test_verdicts_carry_the_failing_check_and_whether_the_judge_answered() -> None:
    cell, task = new_cell_id(_CLOCK), new_task_id(_CLOCK)
    bad, good = new_message_id(_CLOCK), new_message_id(_CLOCK)
    events = [
        *_proposal(
            bad,
            [
                ("capping.checked", {"check": "JUDGE", "outcome": "FAILED", "judge_error": True}),
                (
                    "capping.rejected",
                    {"tier": "SCRATCH_WRITE", "checks_run": 1, "failing_check": "JUDGE"},
                ),
            ],
            cell=cell,
            task=task,
        ),
        *_proposal(
            good,
            [("capping.capped", {"tier": "SCRATCH_WRITE", "checks_passed": 3})],
            cell=cell,
            task=task,
        ),
    ]

    verdicts = {v.proposal_id: v for v in capping_view(events).verdicts}

    assert (verdicts[bad].verdict, verdicts[bad].failing_check, verdicts[bad].judge_error) == (
        "REJECTED",
        "JUDGE",
        True,
    )
    assert (verdicts[good].verdict, verdicts[good].checks_run, verdicts[good].judge_error) == (
        "CAPPED",
        3,
        False,
    )


def test_counts_and_audit_rates_are_per_tier() -> None:
    cell, task = new_cell_id(_CLOCK), new_task_id(_CLOCK)
    ids = [new_message_id(_CLOCK) for _ in range(4)]
    events: list[PheromoneEvent] = []
    for pid in ids:
        events += _proposal(
            pid, [("capping.verified", {"postconditions_held": 1})], cell=cell, task=task
        )
    for second, (pid, outcome) in enumerate(
        [(ids[0], "REJECT"), (ids[1], "APPROVE"), (ids[2], None)], start=10
    ):
        payload: dict[str, JsonValue] = {"tier": "SCRATCH_WRITE", "outcome": outcome}
        if outcome is None:
            payload["judge_error"] = True
        events.append(make_event("capping.audited", pid, after(second), payload=payload))

    view = capping_view(events)

    assert [(c.tier, c.state, c.count) for c in view.by_tier_state] == [
        ("SCRATCH_WRITE", ProposalState.VERIFIED, 4)
    ]
    [rate] = view.audit_rates
    assert (rate.finished, rate.audited, rate.rejected, rate.inconclusive) == (4, 3, 1, 1)
    assert (rate.sampled_fraction, rate.rejection_fraction) == (0.75, 0.5)
    assert {a.outcome for a in view.audits} == {JudgeOutcome.REJECT, JudgeOutcome.APPROVE, None}
    assert all(a.bee_id == _BEE and a.task_id == task for a in view.audits)


def test_a_cells_narrowing_keeps_only_its_own_proposals() -> None:
    mine, theirs = new_cell_id(_CLOCK), new_cell_id(_CLOCK)
    task = new_task_id(_CLOCK)
    one, two = new_message_id(_CLOCK), new_message_id(_CLOCK)
    events = [
        *_proposal(
            one,
            [("capping.rejected", {"tier": "SCRATCH_WRITE", "failing_check": "SCOPE"})],
            cell=mine,
            task=task,
        ),
        *_proposal(two, [], cell=theirs, task=task),
        make_event(
            "capping.audited",
            one,
            after(20),
            payload={"tier": "SCRATCH_WRITE", "outcome": "REJECT"},
        ),
    ]

    narrowed = cappings_on_cell(capping_view(events), mine)

    assert [row.id for row in narrowed.finished] == [one]
    assert narrowed.in_flight == []
    assert [v.proposal_id for v in narrowed.verdicts] == [one]
    assert [a.proposal_id for a in narrowed.audits] == [one]


def test_the_lists_keep_only_the_newest_rows() -> None:
    cell, task = new_cell_id(_CLOCK), new_task_id(_CLOCK)
    events: list[PheromoneEvent] = []
    for _ in range(MAX_ROWS + 3):
        events += _proposal(new_message_id(_CLOCK), [], cell=cell, task=task)

    view = capping_view(events)

    assert len(view.in_flight) == MAX_ROWS
    assert sum(c.count for c in view.by_tier_state) == MAX_ROWS + 3
