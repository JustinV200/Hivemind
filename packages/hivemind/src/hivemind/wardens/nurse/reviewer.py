"""Define ModelNurseReviewer: the model-backed NurseReviewer a Warden's accept path calls.

`hivemind.supervision.capping.checks.nurse.NurseReviewer` is the Protocol seam (that package
never imports `hivemind.llm`); this is the Warden-layer implementation that calls a model,
satisfying it structurally (codingrules section 8.1). One `review` renders `nurse_review.md` over
the request, estimates the call's size, filters the JUDGE chain to the eligible links
(`hivemind.wardens.nurse.eligible`), and runs one `complete_structured` call on them through the
task's own Fanner lane. No eligible link is an answer, UNAVAILABLE ("not nursed"), never an error.

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Built by the Warden's
    composition root (a later step) with the resolved `ModelSlot.JUDGE` binding, the Forage map
    and `Fanner.lane`; called through `hivemind.supervision.capping.run_nurse`. Calls into
    `hivemind.llm`, `hivemind.memory` (the token estimate), `hivemind.forage` and
    `hivemind.supervision.capping` only.

Key invariants:
    - Every call is built fresh from the request alone; nothing accumulates across reviews.
    - A call only ever reaches eligible links: the chain handed to the ladder is re-linked from
      them, so a fallback can never land on a weaker or over-budget model.
    - A malformed, refused or over-long reply becomes NurseAnswerError carrying the chosen
      chain's grade; an outage (ProviderUnavailableError, RateLimitedError) propagates, as it
      does from the judge, because the Hive's Clustering rung handles those.
    - Spend is the winning binding's priced usage; an unpriced call reports 0.0 and the Fanner
      still meters it on the grant.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.wardens.judge for ModelJudgeReviewer, the per-proposal sibling this mirrors.
    - hivemind.supervision.capping.checks.fake for FakeNurseReviewer, the scripted stand-in.
"""

from __future__ import annotations

from collections.abc import Callable

from hivemind.forage import ForageMap
from hivemind.forage.tempo import Tempo
from hivemind.llm import (
    BoundModel,
    CallGate,
    ContextTooLongError,
    LLMRequest,
    MalformedOutputError,
    RefusedError,
    TextPart,
    complete_structured,
)
from hivemind.memory import EstimateCounter, TokenCounter
from hivemind.supervision.capping import NurseAnswer, NurseAnswerError, NurseRequest
from hivemind.wardens.nurse.eligible import CallSize, nurse_chain
from hivemind.wardens.nurse.prompt import NURSE_OUTPUT_TOKENS, build_nurse_request
from hivemind.wardens.nurse.verdict import NurseModelOutput, to_verdict

# NurseAnswerError's detail is bounded here, because a MalformedOutputError's message can carry
# a large slice of raw model output, and this detail is a log line, never a transcript.
MAX_NURSE_ERROR_DETAIL_CHARS = 500

__all__ = ["MAX_NURSE_ERROR_DETAIL_CHARS", "ModelNurseReviewer"]


class ModelNurseReviewer:
    """A NurseReviewer that runs one `complete_structured` call on the eligible JUDGE links."""

    def __init__(
        self,
        bound: BoundModel,
        forage: ForageMap,
        lane_for: Callable[[Tempo], CallGate],
        counter: TokenCounter | None = None,
    ) -> None:
        """Bind this reviewer to the JUDGE chain, the map that grades it and the lane factory.

        Args:
            bound: The resolved `ModelSlot.JUDGE` binding and its fallback chain.
            forage: The Forage map whose effective grades decide eligibility.
            lane_for: Builds the CallGate one review runs through from the task's tempo
                (`Fanner.lane` in production), so the call queues at the task's urgency.
            counter: Estimates the prompt's tokens for the context and budget checks;
                `EstimateCounter()` (characters over a ratio, with a margin) when omitted.
        """
        self._bound = bound
        self._forage = forage
        self._lane_for = lane_for
        self._counter = counter if counter is not None else EstimateCounter()

    async def review(self, request: NurseRequest) -> NurseAnswer:
        """Review `request` on an eligible JUDGE model, or answer UNAVAILABLE when none is.

        Args:
            request: The Warden-built evidence; never the Worker's own account.

        Returns:
            The verdict with the chain's grade and the call's spend, or no verdict and no grade
            when no link is eligible.

        Raises:
            NurseAnswerError: A link was chosen but gave no usable verdict.
        """
        llm_request = build_nurse_request(self._bound.slot, request)
        size = CallSize(
            input_tokens=await self._counter.count(_prompt_text(llm_request)),
            output_tokens=NURSE_OUTPUT_TOKENS,
        )
        chain = nurse_chain(self._bound, request, self._forage, size)
        if chain is None:
            # Nobody strong enough, roomy enough or cheap enough: accepted as "not nursed".
            return NurseAnswer(verdict=None, nurse_grade=None)

        # Latency class: one model call, seconds to tens of seconds; the ladder's rungs and the
        # re-linked fallbacks cover a timeout or a malformed reply, so nothing retries here.
        try:
            result = await complete_structured(
                chain.bound, llm_request, NurseModelOutput, gate=self._lane_for(request.tempo)
            )
        except (MalformedOutputError, RefusedError, ContextTooLongError) as exc:
            detail = str(exc)[:MAX_NURSE_ERROR_DETAIL_CHARS]
            raise NurseAnswerError(detail, nurse_grade=chain.grade) from exc
        spend = result.usage.cost_usd or 0.0

        # A request for changes with no usable finding cannot steer a fix: it is no answer.
        verdict = to_verdict(result.value, len(request.acceptance_criteria))
        if verdict is None:
            raise NurseAnswerError(
                "CHANGES_REQUESTED with no usable finding", nurse_grade=chain.grade, spend=spend
            )
        return NurseAnswer(verdict=verdict, nurse_grade=chain.grade, spend=spend)


def _prompt_text(request: LLMRequest) -> str:
    """Return the request's system prompt and text turns as one string, for the estimate."""
    turns = (
        part.text
        for message in request.messages
        for part in message.parts
        if isinstance(part, TextPart)
    )
    return "\n".join((request.system or "", *turns))
