"""Tests for hivemind.queen.autopilot.table.decide: the Nurse Bee's fix-forward rule (ADR-0061).

Fits into the Hive:
    Mirrors src/hivemind/queen/autopilot/table.py (codingrules section 3); split by feature
    (14.2) from test_table.py, which is near its size limit.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.autopilot.table for the module under test.
"""

from __future__ import annotations

import pytest
from builders.supervision import make_inbox_item
from builders.tasks import make_task

from hivemind.brood_chamber import TaskStatus
from hivemind.queen.autopilot import AttemptTally, QueenAction, decide
from hivemind.supervision import EscalationPolicy, PolicyAction
from waggle.clock import FakeClock
from waggle.ids import new_task_id, new_warden_id
from waggle.messages.labels import HoneyClearance
from waggle.messages.task import (
    NurseFinding,
    NurseOutcome,
    NurseReview,
    NurseReviewStatus,
    NurseVerdict,
    TaskOutcome,
    TaskResult,
)

_ESCALATE = EscalationPolicy(rules=(), default=PolicyAction.ESCALATE)


def _review(rejected: bool) -> NurseReview:
    """A Nurse's CHANGES_REQUESTED review, or an ERROR one (a criterion no Nurse judged)."""
    if not rejected:
        return NurseReview(status=NurseReviewStatus.ERROR, round=1, nurse_grade=3, worker_grade=3)
    finding = NurseFinding(path="out.txt", line=2, problem="It is empty.", fix="Write the poem.")
    verdict = NurseVerdict(
        outcome=NurseOutcome.CHANGES_REQUESTED, findings=(finding,), rubric_id="nurse-review/1"
    )
    return NurseReview(
        status=NurseReviewStatus.REVIEWED, round=1, verdict=verdict, nurse_grade=3, worker_grade=3
    )


@pytest.mark.parametrize(
    ("rejected", "attempt", "tally", "expected"),
    [
        # Rounds left: a fresh Worker fixes forward, even past the ordinary attempt ceiling.
        (True, 3, AttemptTally(made=3, latest=3, nurse_left=1), QueenAction.RETRY_TASK),
        # None left: the human judges it, never a silent FAIL_TASK.
        (True, 1, AttemptTally(made=1, latest=1, nurse_left=0), QueenAction.ESCALATE_TO_HUMAN),
        # A rejection of an attempt a later one already replaced is history.
        (True, 1, AttemptTally(made=2, latest=2, nurse_left=1), QueenAction.RECORD),
        # An ERROR review is no rejection: the ordinary retry rule, never a Nurse round.
        (False, 1, AttemptTally(made=1, latest=1, nurse_left=0), QueenAction.RETRY_TASK),
    ],
)
def test_a_nurse_rejection_is_sent_back_while_rounds_remain_then_escalated(
    rejected: bool, attempt: int, tally: AttemptTally, expected: QueenAction
) -> None:
    clock = FakeClock()
    result = TaskResult(
        task_id=new_task_id(clock),
        attempt=attempt,
        outcome=TaskOutcome.FAILED,
        summary="Wrote out.txt.",
        clearance=HoneyClearance.C1,
        artifacts=(),
        checked_by=new_warden_id(clock),
        handoff=None,
        spend=0.0,
        reason="nurse",
        review=_review(rejected),
    )
    item = make_inbox_item(clock=clock, payload=result)
    task = make_task(status=TaskStatus.RUNNING, clock=clock)

    assert decide(item, task, tally, _ESCALATE, 3) is expected
