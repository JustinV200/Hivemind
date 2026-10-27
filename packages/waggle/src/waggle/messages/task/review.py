"""Define the Nurse Bee's review of an attempt: its findings, its verdict, and the review record.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The Nurse
Bee is a fresh-context reviewer (ADR-0061): once a Warden (the always-on supervisor of one Cell, a
unit of compute) has run a task's deterministic acceptance checks and they pass, it hands a
bounded bundle of evidence to a model that never saw the Worker's (the bee that did the work)
conversation and asks one question: does this meet the objective and its criteria? The answer is
a ``NurseVerdict``: ``PASS``, or ``CHANGES_REQUESTED`` with up to eight ``NurseFinding``s, each
anchored to a criterion, a path or a line. The Warden carries it up on its verified
``TaskResult`` inside a ``NurseReview``, which also says when no Nurse could look (``UNAVAILABLE``,
no eligible model) or the look failed (``ERROR``); and the Queen (the central orchestrator) hands
the findings to a fresh Worker on the same Cell through ``TaskAssign.review_findings`` to fix
forward. Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Value models only, carried by
    waggle.messages.task.reports (TaskResult.review) and waggle.messages.task.assignment
    (TaskAssign.review_findings); calls into waggle.messages.base and
    waggle.messages.forage.values (the model grade bounds) only.

Key invariants:
    - Every model is frozen and forbids extras (VALUE_MODEL_CONFIG).
    - A finding names at least one anchor (a criterion or a path), and a line only with a path.
    - A verdict is PASS exactly when it has no findings, so a PASS can never hide a problem and a
      rejection always says what to fix.
    - A review carries a verdict exactly when its status is REVIEWED, and a Nurse grade unless
      it is UNAVAILABLE (no Nurse was ever chosen); that grade is never below the Worker's.
    - Every problem, fix and note is untrusted model prose, written from evidence that was itself
      untrusted: a receiver delimits it as such before it reaches another model.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - docs/waggle/spec.md section 8.2 for the normative fields, bounds and validators.
    - waggle.messages.task.reports for TaskResult, which carries a NurseReview.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import MAX_PATH_CHARS, VALUE_MODEL_CONFIG
from waggle.messages.forage.values import MAX_MODEL_GRADE, MIN_MODEL_GRADE

MIN_CRITERION_INDEX = 0  # The task's first acceptance criterion.
# The last index TaskAssign.acceptance can hold (MAX_ACCEPTANCE_ITEMS - 1). Restated, not imported,
# because assignment.py imports this module; a test holds the two numbers together.
MAX_CRITERION_INDEX = 31
MIN_FINDING_LINE = 1  # Lines are counted from 1, as every editor and diff shows them.
MAX_FINDING_CHARS = 300  # One problem or one fix: a sentence or two a fresh Worker can act on.
MAX_NURSE_FINDINGS = 8  # The worst few problems; a longer list means the attempt needs a re-plan.
MAX_NURSE_NOTES_CHARS = 1_000  # A short paragraph of overall impression, never a transcript.
MAX_RUBRIC_ID_CHARS = 64  # A rubric's name and version ("nurse-review/1"), stamped by the Nurse.
MIN_NURSE_ROUND = 1  # The first review of a task is round 1, so 0 never passes for a real round.
# A wire ceiling on rounds, well above the manifest's default of 2: the Queen escalates at her
# configured cap, so this only stops a corrupt count from travelling.
MAX_NURSE_ROUND = 8

__all__ = [
    "MAX_CRITERION_INDEX",
    "MAX_FINDING_CHARS",
    "MAX_NURSE_FINDINGS",
    "MAX_NURSE_NOTES_CHARS",
    "MAX_NURSE_ROUND",
    "MAX_RUBRIC_ID_CHARS",
    "MIN_CRITERION_INDEX",
    "MIN_FINDING_LINE",
    "MIN_NURSE_ROUND",
    "NurseFinding",
    "NurseOutcome",
    "NurseReview",
    "NurseReviewStatus",
    "NurseVerdict",
]

# A model grade as the Forage map grades it: 1 (the weakest that can hold a slot) to 5.
_Grade = Annotated[int, Field(ge=MIN_MODEL_GRADE, le=MAX_MODEL_GRADE)]
# One problem or fix sentence: never empty, never more than a fresh Worker reads at a glance.
_FindingText = Annotated[str, Field(min_length=1, max_length=MAX_FINDING_CHARS)]


class NurseOutcome(Enum):
    """What the Nurse decided about an attempt that had already passed its machine checks."""

    PASS = "PASS"  # noqa: S105 (a verdict, not a credential). It meets every criterion.
    CHANGES_REQUESTED = "CHANGES_REQUESTED"  # It does not; the findings say what to fix.


class NurseReviewStatus(Enum):
    """Whether a Nurse looked at the attempt, and if not, why not."""

    REVIEWED = "REVIEWED"  # A Nurse returned a verdict.
    UNAVAILABLE = "UNAVAILABLE"  # No model was eligible; accepted "not nursed", never escalated.
    ERROR = "ERROR"  # A Nurse was chosen but its call or its output failed.


class NurseFinding(BaseModel):
    """One problem the Nurse found, anchored to where it is, with the fix it suggests."""

    model_config = VALUE_MODEL_CONFIG

    criterion_index: (
        Annotated[int, Field(ge=MIN_CRITERION_INDEX, le=MAX_CRITERION_INDEX)] | None
    ) = Field(default=None, description="The acceptance criterion it fails, by index; or None.")
    path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_CHARS)] | None = Field(
        default=None, description="The file the problem is in, as the evidence named it; or None."
    )
    line: Annotated[int, Field(ge=MIN_FINDING_LINE)] | None = Field(
        default=None, description="The line in path, from 1; set only together with path."
    )
    problem: _FindingText = Field(description="What is wrong; untrusted model prose.")
    fix: _FindingText = Field(description="What the next Worker should change; untrusted prose.")

    @model_validator(mode="after")
    def _anchored(self) -> NurseFinding:
        """Require a criterion or a path, and a path under any line."""
        # A finding with no anchor gives the fixing Worker nothing to look at, and a line with no
        # file is a number pointing nowhere; both are refused rather than passed on as noise.
        if self.criterion_index is None and self.path is None:
            raise ValueError("NurseFinding needs a criterion_index or a path to anchor it.")
        if self.line is not None and self.path is None:
            raise ValueError("NurseFinding line is set only together with path.")
        return self


class NurseVerdict(BaseModel):
    """The Nurse's answer: PASS with no findings, or CHANGES_REQUESTED with at least one."""

    model_config = VALUE_MODEL_CONFIG

    outcome: NurseOutcome = Field(description="Whether the attempt meets its objective.")
    findings: tuple[NurseFinding, ...] = Field(
        default=(),
        max_length=MAX_NURSE_FINDINGS,
        description="What to fix; empty exactly when outcome is PASS (validator).",
    )
    rubric_id: str = Field(
        min_length=1,
        max_length=MAX_RUBRIC_ID_CHARS,
        description="The rubric the Nurse judged by, stamped by its implementation, never by the "
        "model.",
    )
    notes: str = Field(
        default="",
        max_length=MAX_NURSE_NOTES_CHARS,
        description="An overall impression; untrusted model prose. Empty by default.",
    )

    @model_validator(mode="after")
    def _pass_has_no_findings(self) -> NurseVerdict:
        """Require findings exactly when the verdict asks for changes."""
        # A PASS with findings would let a problem ride into SUCCEEDED unread, and a rejection with
        # none would send a fresh Worker back with nothing to fix. Both directions are refused.
        if (self.outcome is NurseOutcome.PASS) == bool(self.findings):
            raise ValueError(
                f"NurseVerdict is PASS exactly when it has no findings, got {self.outcome.value} "
                f"with {len(self.findings)} findings."
            )
        return self


class NurseReview(BaseModel):
    """The record of one Nurse round on a Warden's verified result, nursed or not."""

    model_config = VALUE_MODEL_CONFIG

    status: NurseReviewStatus = Field(description="Whether a Nurse looked, and if not, why not.")
    round: Annotated[int, Field(ge=MIN_NURSE_ROUND, le=MAX_NURSE_ROUND)] = Field(
        description="Which review of this task this is, from 1: TaskAssign.review_round plus one."
    )
    verdict: NurseVerdict | None = Field(
        default=None, description="The Nurse's answer; set exactly when status is REVIEWED."
    )
    nurse_grade: _Grade | None = Field(
        default=None,
        description="The effective grade of the model that reviewed; None exactly when status is "
        "UNAVAILABLE, because no model was chosen.",
    )
    worker_grade: _Grade = Field(
        description="The Worker's grade: the highest over its bindings and the models its calls "
        "actually reached (a spill), which the Nurse had to equal."
    )

    @model_validator(mode="after")
    def _status_matches_fields(self) -> NurseReview:
        """Require a verdict only on REVIEWED and a Nurse grade on anything but UNAVAILABLE."""
        # A verdict without a review, or a review without one, would let the Queen act on an
        # answer nobody gave; a grade with no Nurse chosen would claim an eligibility check that
        # never found a model.
        if (self.status is NurseReviewStatus.REVIEWED) != (self.verdict is not None):
            raise ValueError(
                f"NurseReview verdict is set exactly when status is REVIEWED, got "
                f"{self.status.value} with verdict {self.verdict}."
            )
        if (self.status is NurseReviewStatus.UNAVAILABLE) != (self.nurse_grade is None):
            raise ValueError(
                f"NurseReview nurse_grade is None exactly when status is UNAVAILABLE, got "
                f"{self.status.value} with nurse_grade {self.nurse_grade}."
            )
        # The Nurse must be at least as strong as the Worker it reviews (an identical model in a
        # fresh context counts as equal); a weaker one is an eligibility bug, refused at the wire.
        if self.nurse_grade is not None and self.nurse_grade < self.worker_grade:
            raise ValueError(
                f"NurseReview nurse_grade {self.nurse_grade} is below worker_grade "
                f"{self.worker_grade}; a Nurse never reviews a stronger Worker."
            )
        return self
