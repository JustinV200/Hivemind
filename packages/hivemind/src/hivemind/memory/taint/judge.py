"""Define the taint judge: TaintJudge, what it is shown, its verdict, and the model-backed one.

Roadmap step 10.6d (ADR-0043): "Only a judge verdict on the taint rubric, with no shared context,
clears it." This is the same judge machinery the Capping gate's independent review uses
(`hivemind.wardens.judge.ModelJudgeReviewer`, roadmap 4.10), applied to memory: `ModelTaintJudge`
builds one request fresh from a `TaintReview` alone (the item's kind, who labelled it and why,
and the item's own text; never the bee that wrote it, its transcript or anyone's hot state),
renders the taint rubric prompt (`PromptName.TAINT_REVIEW`) around it as retrieved content, and
runs `complete_structured` on the injected `ModelSlot.JUDGE` binding through a `CallGate` (the
seat meter every model call passes through). The model's reply is parsed into a private schema,
never into `TaintVerdict` itself, because the verdict's `rubric_id` must name the rubric this
module actually rendered. A reply the ladder cannot parse becomes `TaintJudgeError`, which the
clearer treats as "keep it tainted": a judge that cannot answer never clears anything.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`. Called
    by `hivemind.memory.taint.clear.clear_taint` through the `TaintJudge` Protocol; a composition
    root builds `ModelTaintJudge` from `ModelSlot.JUDGE` (the same binding the Capping judge uses).
    Calls into `hivemind.llm` (BoundModel, CallGate, LLMRequest, MalformedOutputError, Message,
    PromptName, Role, SectionLabel, complete_structured, render), `hivemind.memory.errors` and
    this package's `marker`.

Key invariants:
    - The request carries only the `TaintReview`'s fields: no shared context (codingrules 8.12).
    - `TaintVerdict.rubric_id` is always `TAINT_RUBRIC_ID`, never a value the model produced.
    - Nothing is accumulated between reviews: every call builds its request fresh (codingrules 8.8).

See Also:
    - hivemind.wardens.judge for ModelJudgeReviewer, the shape this module mirrors.
    - hivemind.llm.prompts.taint_review for the rubric this module renders.
    - hivemind.memory.taint.clear for the one caller.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm import (
    BoundModel,
    CallGate,
    LLMRequest,
    MalformedOutputError,
    Message,
    PromptName,
    Role,
    SectionLabel,
    complete_structured,
    render,
)
from hivemind.llm.prompts import neutralise_fences
from hivemind.memory.errors import TaintJudgeError
from hivemind.memory.taint.marker import MAX_TAINT_REASON_CHARS, TaintedKind, TaintSource

TAINT_RUBRIC_ID = "taint.v1"  # The rubric taint_review.md renders; bump it when the rubric changes.
# The most text one review may show the judge (about 4,000 tokens): a larger item is never cleared
# on a partial reading (the clearer refuses to review it) rather than judged on its head alone.
MAX_REVIEW_CHARS = 16_000
# A verdict is short, but a reasoning model's thinking counts against this too: the Capping
# judge's own first real run needed this much room to answer at all (hivemind.wardens.judge).
TAINT_JUDGE_OUTPUT_TOKENS = 4_096
MAX_TAINT_REASONS = 8  # A rubric has four lines; more than eight reasons is not a short list.
MAX_TAINT_REASON_TEXT_CHARS = 500  # One line per reason.
_MAX_JUDGE_ERROR_DETAIL_CHARS = 500  # A judge failure's detail, never the model's raw output.
_ONE_LINE_INSTRUCTION = "Judge the item shown above against the taint rubric: CLEAR or KEEP."

__all__ = [
    "MAX_REVIEW_CHARS",
    "TAINT_JUDGE_OUTPUT_TOKENS",
    "TAINT_RUBRIC_ID",
    "ModelTaintJudge",
    "TaintJudge",
    "TaintJudgement",
    "TaintReview",
    "TaintVerdict",
]

_Reason = Annotated[str, Field(max_length=MAX_TAINT_REASON_TEXT_CHARS)]


class TaintJudgement(Enum):
    """The taint judge's two answers."""

    CLEAR = "CLEAR"  # Every rubric line holds: the item may reach prompts again.
    KEEP = "KEEP"  # A line fails, or the judge is unsure: the item stays refused.


class TaintReview(BaseModel):
    """Everything a taint review shows the judge, and nothing else (no shared context)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TaintedKind = Field(description="What kind of item is under review.")
    source: TaintSource = Field(description="Which setter labelled it.")
    reason: str = Field(max_length=MAX_TAINT_REASON_CHARS, description="Why it was labelled.")
    content: str = Field(
        max_length=MAX_REVIEW_CHARS, description="The item's own text, whole; never truncated."
    )


class TaintVerdict(BaseModel):
    """One taint review's structured answer: CLEAR or KEEP, with reasons, against one rubric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgement: TaintJudgement = Field(description="CLEAR or KEEP.")
    reasons: tuple[_Reason, ...] = Field(
        default=(), max_length=MAX_TAINT_REASONS, description="Which rubric lines held or failed."
    )
    rubric_id: str = Field(description="The rubric this verdict was scored against.")


class TaintJudge(Protocol):
    """Review one tainted item independently and say whether it may be cleared."""

    async def review(self, review: TaintReview) -> TaintVerdict:
        """Judge `review` against the taint rubric.

        Args:
            review: The item's kind, setter, reason and whole text; nothing else.

        Returns:
            CLEAR or KEEP, with reasons.

        Raises:
            hivemind.memory.errors.TaintJudgeError: The judge could not produce a verdict.
        """
        ...


class _TaintModelOutput(BaseModel):
    """The model's own reply: everything but the rubric id, which this module fills in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgement: TaintJudgement = Field(description="CLEAR or KEEP.")
    reasons: tuple[str, ...] = Field(default=(), description="Why, as a short list.")


class ModelTaintJudge:
    """A TaintJudge that runs `complete_structured` on `ModelSlot.JUDGE` through a CallGate."""

    def __init__(self, bound: BoundModel, gate: CallGate) -> None:
        """Bind this judge to a JUDGE binding and the lane its calls run through.

        Args:
            bound: The `ModelSlot.JUDGE` binding, typically pinned to a different provider than
                `WORKER` so blind spots do not correlate (codingrules 8.12).
            gate: The seat meter lane every review runs through (a Fanner lane in production).
        """
        self._bound = bound
        self._gate = gate

    async def review(self, review: TaintReview) -> TaintVerdict:
        """Judge `review` against the taint rubric; see `TaintJudge.review`."""
        request = _build_request(self._bound, review)
        # Latency class: one model call, seconds to tens of seconds; the ladder retries rungs and
        # walks the binding's fallback chain itself. An outage (ProviderUnavailableError) is not
        # caught: it is Clustering's to handle, not a verdict on the item.
        try:
            result = await complete_structured(
                self._bound, request, _TaintModelOutput, gate=self._gate
            )
        except MalformedOutputError as exc:
            raise TaintJudgeError(str(exc)[:_MAX_JUDGE_ERROR_DETAIL_CHARS]) from exc
        output = result.value
        return TaintVerdict(
            judgement=output.judgement,
            reasons=tuple(
                r[:MAX_TAINT_REASON_TEXT_CHARS] for r in output.reasons[:MAX_TAINT_REASONS]
            ),
            rubric_id=TAINT_RUBRIC_ID,
        )


def _build_request(bound: BoundModel, review: TaintReview) -> LLMRequest:
    """Build the one request `review` makes, fresh from the `TaintReview` alone."""
    # The item is the thing under suspicion: its text is neutralised so a copied delimiter can
    # never close the retrieved section early and speak to the judge from outside it.
    shown = "\n".join(
        [
            f"Item kind: {review.kind.value}",
            f"Labelled tainted by: {review.source.value}",
            f"Why: {neutralise_fences(review.reason)}",
            "The item's own text follows, whole:",
            neutralise_fences(review.content),
        ]
    )
    system = render(PromptName.TAINT_REVIEW, sections={SectionLabel.RETRIEVED: shown})
    return LLMRequest(
        slot=bound.slot,
        system=system,
        messages=(Message.text(Role.USER, _ONE_LINE_INSTRUCTION),),
        max_output_tokens=TAINT_JUDGE_OUTPUT_TOKENS,
    )
