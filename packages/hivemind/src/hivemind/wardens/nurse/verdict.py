"""Define the Nurse model's reply schema and turn a reply into the wire's NurseVerdict.

The model answers in `NurseModelOutput`, a private shape looser than `NurseVerdict` on purpose:
a reply with one over-long sentence or one finding pointing at a criterion that does not exist
should cost that one finding, not the whole structured-output ladder's retries. `to_verdict`
then keeps every usable finding (bounded, anchored to a real criterion or a path), stamps this
Nurse's own rubric id, and fails closed: findings mean CHANGES_REQUESTED whatever the model's
outcome said, and CHANGES_REQUESTED with no usable finding is no verdict at all.

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Called by
    `hivemind.wardens.nurse.reviewer.ModelNurseReviewer.review` on each structured reply. Calls
    into waggle only.

Key invariants:
    - `rubric_id` is always NURSE_RUBRIC_ID, never model text (the judge's rule, wardens.judge).
    - A PASS verdict is returned only when the model said PASS and named no usable finding.
    - Every returned finding satisfies NurseFinding's bounds; an unusable one is dropped, never
      repaired into an anchor the model did not give.

See Also:
    - waggle.messages.task.review for NurseVerdict and NurseFinding, the wire shapes built here.
    - hivemind/llm/prompts/nurse_review.md for what the model is asked to return.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from waggle.messages.base import MAX_PATH_CHARS
from waggle.messages.task import NurseFinding, NurseOutcome, NurseVerdict
from waggle.messages.task.review import (
    MAX_FINDING_CHARS,
    MAX_NURSE_FINDINGS,
    MAX_NURSE_NOTES_CHARS,
    MIN_FINDING_LINE,
)

NURSE_RUBRIC_ID = "nurse-review/1"  # Names nurse_review.md's contract; bump it when that changes.

__all__ = ["NURSE_RUBRIC_ID", "NurseModelFinding", "NurseModelOutput", "to_verdict"]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class NurseModelFinding(BaseModel):
    """One problem as the model states it, before `to_verdict` bounds and anchors it."""

    model_config = _FROZEN

    criterion_index: int | None = Field(default=None, description="The criterion it fails.")
    path: str | None = Field(default=None, description="The file the problem is in.")
    line: int | None = Field(default=None, description="The line in that file, from 1.")
    problem: str = Field(description="What is wrong, in a sentence or two.")
    fix: str = Field(description="What the next Worker should change, in a sentence or two.")


class NurseModelOutput(BaseModel):
    """The model's whole reply: its outcome, its findings and a short note."""

    model_config = _FROZEN

    outcome: NurseOutcome = Field(description="PASS, or CHANGES_REQUESTED.")
    findings: tuple[NurseModelFinding, ...] = Field(
        default=(), description="Each problem; empty for PASS."
    )
    notes: str = Field(default="", description="A short paragraph at most.")


def to_verdict(output: NurseModelOutput, criteria_count: int) -> NurseVerdict | None:
    """Turn the model's reply into a NurseVerdict, or None when it asked for changes yet named none.

    Args:
        output: The parsed reply.
        criteria_count: How many acceptance criteria the request held, to check each index.

    Returns:
        PASS when the model passed and named no usable finding; CHANGES_REQUESTED with the
        usable findings (at most MAX_NURSE_FINDINGS) when there is one; None when it said
        CHANGES_REQUESTED with nothing usable, which is no answer.
    """
    usable = [
        f for f in (_finding(raw, criteria_count) for raw in output.findings) if f is not None
    ]
    findings = tuple(usable[:MAX_NURSE_FINDINGS])
    notes = output.notes[:MAX_NURSE_NOTES_CHARS]

    # Fail closed: a named problem outweighs the model's own PASS.
    if findings:
        outcome = NurseOutcome.CHANGES_REQUESTED
    elif output.outcome is NurseOutcome.PASS:
        outcome = NurseOutcome.PASS
    else:
        return None
    return NurseVerdict(outcome=outcome, findings=findings, rubric_id=NURSE_RUBRIC_ID, notes=notes)


def _finding(raw: NurseModelFinding, criteria_count: int) -> NurseFinding | None:
    """Bound and anchor one model finding, or None when nothing usable is left of it."""
    # Keep only anchors that point at something real; a line means nothing without its file.
    index = raw.criterion_index
    if index is not None and not 0 <= index < criteria_count:
        index = None
    path = raw.path if raw.path and len(raw.path) <= MAX_PATH_CHARS else None
    line = raw.line if path is not None and raw.line is not None else None
    if line is not None and line < MIN_FINDING_LINE:
        line = None

    # An unanchored finding, or one with no words left, cannot tell the next Worker anything.
    problem = raw.problem.strip()[:MAX_FINDING_CHARS]
    fix = raw.fix.strip()[:MAX_FINDING_CHARS]
    if (index is None and path is None) or not problem or not fix:
        return None
    return NurseFinding(criterion_index=index, path=path, line=line, problem=problem, fix=fix)
