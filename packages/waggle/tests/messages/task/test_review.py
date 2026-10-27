"""Pin protocol 1.15's Nurse Bee schema: findings, verdict, review and where they go (ADR-0061).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.messages.task.review against spec
    section 8.2, and the two fields that carry it: TaskResult.review and TaskAssign's
    review_round and review_findings, each defaulting so an older peer's message still validates.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 8.2 for the review models and section 4 for minor 1.15.
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.task import (
    NurseFinding,
    NurseReview,
    NurseVerdict,
    TaskAssign,
    TaskOutcome,
    TaskResult,
)
from waggle.messages.task.assignment import MAX_ACCEPTANCE_ITEMS
from waggle.messages.task.review import (
    MAX_CRITERION_INDEX,
    MAX_FINDING_CHARS,
    MAX_NURSE_FINDINGS,
    MAX_NURSE_ROUND,
)

_CLOCK = FakeClock()
_FINDING = {"criterion_index": 0, "problem": "The summary skips section 3.", "fix": "Cover it."}
_PASS = {"outcome": "PASS", "rubric_id": "nurse-review/1"}
_REJECT = {"outcome": "CHANGES_REQUESTED", "findings": [_FINDING], "rubric_id": "nurse-review/1"}


def _review(**changes: object) -> dict[str, object]:
    """A REVIEWED PASS review as a wire dict, with `changes` applied."""
    fields: dict[str, object] = {
        "status": "REVIEWED",
        "round": 1,
        "verdict": _PASS,
        "nurse_grade": 4,
        "worker_grade": 3,
    }
    return fields | changes


def _result(outcome: TaskOutcome, **changes: object) -> dict[str, object]:
    """A TaskResult as a wire dict: a Worker's claim when CLAIMED, else a Warden's result."""
    claimed = outcome is TaskOutcome.CLAIMED
    fields: dict[str, object] = {
        "task_id": new_id(IdKind.TASK, _CLOCK),
        "attempt": 1,
        "outcome": outcome.value,
        "summary": "Done.",
        "clearance": "C1",
        "artifacts": [],
        "checked_by": None if claimed else new_id(IdKind.WARDEN, _CLOCK),
        "handoff": None,
        "spend": 0.0,
        "reason": "Acceptance passed.",
    }
    return fields | changes


def _assign(**changes: object) -> dict[str, object]:
    """A minimal TaskAssign as a wire dict, with `changes` applied."""
    task_id = new_id(IdKind.TASK, _CLOCK)
    fields: dict[str, object] = {
        "task_id": task_id,
        "goal_id": task_id,
        "cell_id": new_id(IdKind.CELL, _CLOCK),
        "role": "FORAGER",
        "slot": "WORKER",
        "objective": "Summarise the notes.",
        "acceptance": [
            {"kind": "FILE_EXISTS", "subject": "summary.md", "argv": [], "expected": None}
        ],
        "tempo": {"latency_budget_s": None, "accuracy": "NORMAL"},
        "clearance": "C1",
        "grant_id": new_id(IdKind.GRANT, _CLOCK),
        "attempt": 1,
        "resume_from": None,
        "reason": "Placed.",
    }
    return fields | changes


def test_a_criterion_index_fits_every_acceptance_slot() -> None:
    # Restated in review.py to avoid an import cycle; this holds the two numbers together.
    assert MAX_CRITERION_INDEX == MAX_ACCEPTANCE_ITEMS - 1


@pytest.mark.parametrize(
    ("changes", "valid"),
    [
        ({}, True),
        ({"criterion_index": None, "path": "src/app.py", "line": 12}, True),
        ({"criterion_index": None}, False),  # No anchor at all.
        ({"line": 12}, False),  # A line with no file.
        ({"criterion_index": MAX_CRITERION_INDEX + 1}, False),
        ({"problem": ""}, False),
        ({"fix": "x" * (MAX_FINDING_CHARS + 1)}, False),
    ],
)
def test_a_finding_must_be_anchored_and_bounded(changes: dict[str, object], valid: bool) -> None:
    if valid:
        NurseFinding.model_validate(_FINDING | changes)
        return
    with pytest.raises(ValidationError):
        NurseFinding.model_validate(_FINDING | changes)


@pytest.mark.parametrize(
    ("model", "payload", "valid"),
    [
        (NurseVerdict, _PASS, True),
        (NurseVerdict, _REJECT, True),
        (NurseVerdict, _PASS | {"findings": [_FINDING]}, False),  # A PASS hiding a problem.
        (NurseVerdict, _REJECT | {"findings": []}, False),  # A rejection with nothing to fix.
        (NurseVerdict, _REJECT | {"findings": [_FINDING] * (MAX_NURSE_FINDINGS + 1)}, False),
        (NurseReview, _review(), True),
        (NurseReview, _review(nurse_grade=3), True),  # An identical model counts as equal.
        (NurseReview, _review(status="UNAVAILABLE", verdict=None, nurse_grade=None), True),
        (NurseReview, _review(status="ERROR", verdict=None), True),
        (NurseReview, _review(verdict=None), False),  # REVIEWED with no answer.
        (NurseReview, _review(status="ERROR"), False),  # An answer nobody gave.
        (NurseReview, _review(status="UNAVAILABLE", verdict=None), False),  # A grade, no Nurse.
        (NurseReview, _review(nurse_grade=2), False),  # A Nurse weaker than the Worker.
        (NurseReview, _review(round=0), False),
    ],
)
def test_verdict_and_review_fields_agree_with_their_status(
    model: type[NurseVerdict | NurseReview], payload: dict[str, object], valid: bool
) -> None:
    if valid:
        assert model.model_validate(payload) == model.model_validate_json(
            model.model_validate(payload).model_dump_json()
        )
        return
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("outcome", "review", "valid"),
    [
        (TaskOutcome.SUCCEEDED, None, True),  # An older peer's result: no review key needed.
        (TaskOutcome.SUCCEEDED, _review(), True),
        (TaskOutcome.FAILED, _review(verdict=_REJECT), True),
        (TaskOutcome.SUCCEEDED, _review(verdict=_REJECT), False),  # A rejection that succeeds.
        (TaskOutcome.CLAIMED, _review(), False),  # A Worker reviewing its own work.
    ],
)
def test_task_result_carries_a_review_only_where_it_can_be_true(
    outcome: TaskOutcome, review: dict[str, object] | None, valid: bool
) -> None:
    payload = _result(outcome) if review is None else _result(outcome, review=review)
    if not valid:
        with pytest.raises(ValidationError):
            TaskResult.model_validate(payload)
        return
    result = TaskResult.model_validate(payload)
    assert TaskResult.model_validate(result.model_dump(mode="json")) == result


@pytest.mark.parametrize(
    ("changes", "valid"),
    [
        ({}, True),  # An older peer's assign: both fields default.
        ({"review_round": 1, "review_findings": [_FINDING]}, True),
        ({"review_findings": [_FINDING]}, False),  # Findings no Nurse wrote.
        ({"review_round": MAX_NURSE_ROUND}, False),  # Its review would be past the ceiling.
    ],
)
def test_task_assign_carries_findings_only_after_a_review(
    changes: dict[str, object], valid: bool
) -> None:
    if not valid:
        with pytest.raises(ValidationError):
            TaskAssign.model_validate(_assign(**changes))
        return
    assign = TaskAssign.model_validate(_assign(**changes))
    assert assign.review_round == changes.get("review_round", 0)
    assert TaskAssign.model_validate(assign.model_dump(mode="json")) == assign
