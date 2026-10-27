"""Define the Nurse Bee seam: NurseRequest, NurseAnswer, the NurseReviewer Protocol and run_nurse.

The Nurse Bee (ADR-0061) is a fresh-context reviewer. Once a Warden's deterministic acceptance
checks pass on an attempt, it hands a model that never saw the Worker's conversation a bounded
bundle of evidence and asks whether the work meets its objective. This module is the seam that
review crosses (codingrules section 8.1): `NurseRequest` is everything the Nurse may see,
`NurseReviewer` is the Protocol a model-backed Nurse at the Warden layer satisfies, `NurseAnswer`
is what it hands back, and `run_nurse` turns that answer, or its `NurseAnswerError`, into the
wire's `NurseReview`. This package never imports `hivemind.llm` (an autopilot boundary,
codingrules section 4), so choosing a model, rendering the prompt and calling it live elsewhere.

"Fresh context" is `NurseRequest`'s whole shape. It holds what the Warden itself observed: the
objective, the criteria with their deterministic outcomes, the actions of the attempt's VERIFIED
proposals with no proposer id, the claimed artifacts by reference, and bounded reads of the
touched files. It never holds the Worker's transcript, hot state, Handoff, a proposal's reason or
the claim's summary; each of those is the Worker's own account of its work.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    checks`. Built by a Warden's accept path (`hivemind.wardens.ticks.results.handle_accept`, a
    later step); implemented by `hivemind.wardens.nurse` and by `checks.fake.FakeNurseReviewer`.
    Calls into `hivemind.forage.tempo`, `hivemind.supervision.capping.errors`, `hivemind.
    supervision.capping.postconditions`, `hivemind.supervision.capping.checks.judge` (the goal
    bound) and waggle only.

Key invariants:
    - Every model here is frozen and forbids extras, like every boundary value in this package.
    - A request's file text is RETRIEVED, untrusted content: the Warden scans it through the Guard
      before it lands here, and a renderer delimits it before a model reads it.
    - A request's files together hold at most MAX_NURSE_EVIDENCE_CHARS; the manifest's
      `[nurse] max_evidence_chars` sets a tighter budget below that ceiling.
    - An answer has a verdict exactly when it has a Nurse grade: no grade means no model was
      eligible (UNAVAILABLE), never a verdict nobody gave.
    - `run_nurse` never lets a NurseAnswerError escape; an outage is not one and propagates.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - waggle.messages.task.review for NurseVerdict and NurseReview, the wire shapes this returns.
    - hivemind.supervision.capping.checks.judge for JudgeReviewer, the per-proposal sibling seam.
    - hivemind.supervision.capping.checks.fake for FakeNurseReviewer.
    - hivemind.supervision.capping.errors for NurseAnswerError.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage.tempo import Tempo
from hivemind.supervision.capping.checks.judge import MAX_GOAL_CHARS
from hivemind.supervision.capping.errors import NurseAnswerError
from hivemind.supervision.capping.postconditions import PostconditionOutcome
from waggle.messages.base import MAX_PATH_CHARS
from waggle.messages.capping import ProposedAction
from waggle.messages.forage.values import MAX_MODEL_GRADE, MIN_MODEL_GRADE
from waggle.messages.labels import Postcondition
from waggle.messages.task import ArtifactRef, NurseReview, NurseReviewStatus, NurseVerdict
from waggle.messages.task.assignment import MAX_ACCEPTANCE_ITEMS

MAX_NURSE_ACTIONS = 64  # One attempt's verified proposals; more means the task was too big.
MAX_NURSE_FILES = 32  # Touched files read for the Nurse; the rest are named by their actions.
# A hard ceiling on all file text in one request, so a misconfigured budget cannot flood a model's
# context; the manifest's max_evidence_chars picks the working budget beneath it.
MAX_NURSE_EVIDENCE_CHARS = 100_000

__all__ = [
    "MAX_NURSE_ACTIONS",
    "MAX_NURSE_EVIDENCE_CHARS",
    "MAX_NURSE_FILES",
    "NurseAnswer",
    "NurseFile",
    "NurseRequest",
    "NurseReviewer",
    "run_nurse",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")
# A model grade as the Forage map grades it, matching NurseReview's own bound.
_Grade = Annotated[int, Field(ge=MIN_MODEL_GRADE, le=MAX_MODEL_GRADE)]


class NurseFile(BaseModel):
    """One touched file as the Warden read it through its own session: untrusted content."""

    model_config = _FROZEN

    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS, description="The file read.")
    text: str = Field(description="What was read, after the Guard's scan; untrusted.")
    truncated: bool = Field(
        default=False, description="True when text is a prefix cut to the evidence budget."
    )


class NurseRequest(BaseModel):
    """Everything a fresh-context Nurse review of one attempt may see, and nothing else.

    Deliberately excludes the Worker's id, transcript, hot state and Handoff, every proposal's
    reason and the claim's summary: the Nurse reviews what was done, never the doer's account.
    """

    model_config = _FROZEN

    goal: str = Field(
        min_length=1, max_length=MAX_GOAL_CHARS, description="The task's objective, capped."
    )
    acceptance_criteria: tuple[Postcondition, ...] = Field(
        max_length=MAX_ACCEPTANCE_ITEMS, description="The task's criteria, as the Queen set them."
    )
    outcomes: tuple[PostconditionOutcome, ...] = Field(
        max_length=MAX_ACCEPTANCE_ITEMS,
        description="What deterministic acceptance observed for each criterion it could check.",
    )
    actions: tuple[ProposedAction, ...] = Field(
        max_length=MAX_NURSE_ACTIONS,
        description="The attempt's VERIFIED proposals' actions, in order, with no proposer id.",
    )
    artifacts: tuple[ArtifactRef, ...] = Field(
        default=(), description="The claimed outputs, by reference; contents never travel."
    )
    files: tuple[NurseFile, ...] = Field(
        default=(), max_length=MAX_NURSE_FILES, description="Bounded reads of touched files."
    )
    worker_grade: _Grade = Field(
        description="The Worker's grade, the highest over its bindings and the models its calls "
        "reached; a Nurse must be at least this strong."
    )
    tempo: Tempo = Field(
        default_factory=Tempo,
        description="The task's tempo: orders the Nurse's call in the Fanner's queues only.",
    )

    @model_validator(mode="after")
    def _bounded_and_consistent(self) -> NurseRequest:
        """Cap the total file text and tie every outcome to a criterion that exists."""
        # Each file is bounded only by the total, so one large file can use the whole budget; the
        # Warden decides how to share it, this only stops a request from exceeding it.
        total = sum(len(f.text) for f in self.files)
        if total > MAX_NURSE_EVIDENCE_CHARS:
            raise ValueError(
                f"NurseRequest files hold {total} chars, over {MAX_NURSE_EVIDENCE_CHARS}."
            )
        # An outcome for a criterion that is not in the request would tell the Nurse a check
        # passed or failed on something it cannot read.
        if any(o.index >= len(self.acceptance_criteria) for o in self.outcomes):
            raise ValueError("NurseRequest outcome index has no matching acceptance criterion.")
        return self


class NurseAnswer(BaseModel):
    """What a NurseReviewer hands back: a verdict and its model's grade, or neither, plus spend."""

    model_config = _FROZEN

    verdict: NurseVerdict | None = Field(
        description="The Nurse's verdict; None exactly when no model was eligible."
    )
    nurse_grade: _Grade | None = Field(
        description="The effective grade of the model that reviewed; None exactly when no model "
        "was eligible."
    )
    spend: float = Field(default=0.0, ge=0.0, description="What the Nurse's call cost.")

    @model_validator(mode="after")
    def _verdict_with_grade(self) -> NurseAnswer:
        """Require a grade exactly when there is a verdict."""
        # A verdict with no grade would hide whether the reviewer was strong enough; a grade with
        # no verdict is a chosen model that failed, which is NurseAnswerError's job, not this.
        if (self.verdict is None) != (self.nurse_grade is None):
            raise ValueError("NurseAnswer has a verdict exactly when it has a nurse_grade.")
        return self


class NurseReviewer(Protocol):
    """Review one attempt in a fresh context and say PASS or what to fix.

    An implementation picks an eligible model (grade at least `request.worker_grade`), returns a
    `NurseAnswer` with no verdict when none is eligible, and raises `NurseAnswerError` when the
    chosen model's call or output fails. An outage is not an answer failure: it propagates.
    Implementations must be safe to call concurrently, since a Warden may accept several tasks.
    """

    async def review(self, request: NurseRequest) -> NurseAnswer:
        """Review `request` and return the Nurse's answer.

        Args:
            request: The Warden-built evidence; never the Worker's own account.

        Returns:
            A verdict with its model's grade, or neither when no model was eligible, plus spend.

        Raises:
            NurseAnswerError: A model was chosen but could not produce a verdict.
        """
        ...


async def run_nurse(
    reviewer: NurseReviewer, request: NurseRequest, round_: int
) -> tuple[NurseReview, float]:
    """Run one Nurse round and record it as the wire's NurseReview, with what it cost.

    Args:
        reviewer: The Nurse to ask.
        request: The evidence for this attempt.
        round_: Which review of the task this is, from 1 (TaskAssign.review_round plus one).

    Returns:
        The review (REVIEWED, UNAVAILABLE or ERROR) and the spend to add to TaskResult.spend.

    Raises:
        pydantic.ValidationError: The reviewer answered with a grade below the Worker's, a bug in
            its eligibility check that the wire refuses rather than records.
    """
    # Latency class: one structured model call, seconds to tens of seconds; timeouts and retries
    # belong to the reviewer implementation, not to this seam.
    try:
        answer = await reviewer.review(request)
    except NurseAnswerError as exc:
        # A chosen Nurse that cannot answer is a recorded ERROR, never a crash of the accept path.
        review = NurseReview(
            status=NurseReviewStatus.ERROR,
            round=round_,
            nurse_grade=exc.nurse_grade,
            worker_grade=request.worker_grade,
        )
        return review, exc.spend
    status = NurseReviewStatus.UNAVAILABLE if answer.verdict is None else NurseReviewStatus.REVIEWED
    review = NurseReview(
        status=status,
        round=round_,
        verdict=answer.verdict,
        nurse_grade=answer.nurse_grade,
        worker_grade=request.worker_grade,
    )
    return review, answer.spend
