"""Unit tests for hivemind.supervision.capping.checks.nurse and FakeNurseReviewer (ADR-0061)."""

from __future__ import annotations

import pytest
from builders.capping import make_nurse_request
from pydantic import ValidationError

from hivemind.supervision.capping.checks.fake import FakeNurseReviewer
from hivemind.supervision.capping.checks.nurse import (
    MAX_NURSE_EVIDENCE_CHARS,
    NurseAnswer,
    NurseFile,
    run_nurse,
)
from hivemind.supervision.capping.errors import NurseAnswerError
from hivemind.supervision.capping.postconditions import PostconditionOutcome
from waggle.messages.labels import PostconditionKind
from waggle.messages.task import (
    NurseFinding,
    NurseOutcome,
    NurseReviewStatus,
    NurseVerdict,
)

_PASS = NurseVerdict(outcome=NurseOutcome.PASS, rubric_id="nurse-review/1")
_CHANGES = NurseVerdict(
    outcome=NurseOutcome.CHANGES_REQUESTED,
    findings=(NurseFinding(criterion_index=0, problem="Note is empty.", fix="Write the note."),),
    rubric_id="nurse-review/1",
)


@pytest.mark.parametrize(
    "overrides",
    [
        # More file text than the hard ceiling, split so no single field bound catches it.
        {
            "files": (
                NurseFile(path="a.txt", text="x" * MAX_NURSE_EVIDENCE_CHARS),
                NurseFile(path="b.txt", text="y"),
            )
        },
        # An outcome for criterion 1 when the request holds only criterion 0.
        {
            "outcomes": (
                PostconditionOutcome(index=1, kind=PostconditionKind.FILE_EXISTS, has_held=True),
            )
        },
        # The claim's summary is the Worker's own account, so the request has no field for it.
        {"summary": "I did everything."},
    ],
)
def test_nurse_request_refuses_oversized_or_inconsistent_evidence(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_nurse_request(**overrides)


@pytest.mark.parametrize(("verdict", "grade"), [(_PASS, None), (None, 3)])
def test_nurse_answer_pairs_a_verdict_with_a_grade(
    verdict: NurseVerdict | None, grade: int | None
) -> None:
    with pytest.raises(ValidationError):
        NurseAnswer(verdict=verdict, nurse_grade=grade)


@pytest.mark.parametrize(
    ("scripted", "status", "nurse_grade", "spend"),
    [
        (
            NurseAnswer(verdict=_PASS, nurse_grade=4, spend=0.02),
            NurseReviewStatus.REVIEWED,
            4,
            0.02,
        ),
        (NurseAnswer(verdict=_CHANGES, nurse_grade=3), NurseReviewStatus.REVIEWED, 3, 0.0),
        (NurseAnswer(verdict=None, nurse_grade=None), NurseReviewStatus.UNAVAILABLE, None, 0.0),
        (NurseAnswerError("bad json", nurse_grade=5, spend=0.01), NurseReviewStatus.ERROR, 5, 0.01),
        # An empty queue fails like a chosen Nurse that could not answer, at the Worker's grade.
        (None, NurseReviewStatus.ERROR, 3, 0.0),
    ],
)
async def test_run_nurse_records_each_answer_as_a_review_with_its_spend(
    scripted: NurseAnswer | NurseAnswerError | None,
    status: NurseReviewStatus,
    nurse_grade: int | None,
    spend: float,
) -> None:
    reviewer = FakeNurseReviewer() if scripted is None else FakeNurseReviewer(scripted)
    request = make_nurse_request()

    review, cost = await run_nurse(reviewer, request, 2)

    assert (review.status, review.round, review.nurse_grade, review.worker_grade, cost) == (
        status,
        2,
        nurse_grade,
        3,
        spend,
    )
    assert review.verdict == (scripted.verdict if isinstance(scripted, NurseAnswer) else None)
    assert reviewer.calls == [request]


async def test_run_nurse_refuses_a_nurse_weaker_than_the_worker() -> None:
    # An eligibility bug in a reviewer is refused at the wire, never recorded as a review.
    reviewer = FakeNurseReviewer(NurseAnswer(verdict=_PASS, nurse_grade=2))

    with pytest.raises(ValidationError):
        await run_nurse(reviewer, make_nurse_request(worker_grade=3), 1)
