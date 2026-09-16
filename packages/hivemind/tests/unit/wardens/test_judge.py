"""Tests for hivemind.wardens.judge: ModelJudgeReviewer.

Fits into the Hive:
    Mirrors src/hivemind/wardens/judge.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.judge for ModelJudgeReviewer, the class under test.
    - hivemind.supervision.capping.checks.judge for JudgeRequest/JudgeVerdict/JudgeOutcome.
"""

from __future__ import annotations

import json

from builders.capping import make_judge_request
from builders.llm import make_bound

from hivemind.forage.slots import ModelSlot
from hivemind.llm import DirectCallGate, FakeLLMProvider, LLMRequest, StopReason, TextPart, Usage
from hivemind.llm.models import LLMResponse
from hivemind.supervision.capping import JudgeOutcome
from hivemind.wardens.judge import ModelJudgeReviewer

_MODEL_ID = "test-model"


def _verdict_response(outcome: str, *, reasons: list[str] | None = None) -> LLMResponse:
    """Build an LLMResponse carrying raw JSON shaped like `_JudgeModelOutput`.

    Full-capability `FakeLLMProvider` starts `complete_structured` on the NATIVE rung, which reads
    `response.text` as JSON directly (no fenced block -- that is the PROMPTED rung's own shape,
    `hivemind.llm.ladders.extraction.extract_json_block`).
    """
    body = {"outcome": outcome, "reasons": reasons or [], "notes": "looks fine"}
    return LLMResponse(
        parts=(TextPart(text=json.dumps(body)),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=10, output_tokens=5),
        model=_MODEL_ID,
    )


async def test_review_builds_a_verdict_with_the_requests_own_rubric_id() -> None:
    """The returned JudgeVerdict.rubric_id is always request.rubric.rubric_id, never model text."""
    provider = FakeLLMProvider(name="judge-provider")
    provider.script(_verdict_response("APPROVE", reasons=["Matches its stated summary."]))
    bound = make_bound(slot=ModelSlot.JUDGE, provider=provider)
    reviewer = ModelJudgeReviewer(bound=bound, lane_for=lambda _tempo: DirectCallGate())
    request = make_judge_request()

    verdict = await reviewer.review(request)

    assert verdict.outcome is JudgeOutcome.APPROVE
    assert verdict.reasons == ("Matches its stated summary.",)
    assert verdict.rubric_id == request.rubric.rubric_id


async def test_review_never_shares_the_proposers_transcript_or_hot_state() -> None:
    """The one LLMRequest sent carries only request's own fields -- no proposer id, no history."""
    provider = FakeLLMProvider(name="judge-provider")
    provider.script(_verdict_response("REJECT", reasons=["Touches an undeclared path."]))
    bound = make_bound(slot=ModelSlot.JUDGE, provider=provider)
    reviewer = ModelJudgeReviewer(bound=bound, lane_for=lambda _tempo: DirectCallGate())
    request = make_judge_request()

    verdict = await reviewer.review(request)

    assert verdict.outcome is JudgeOutcome.REJECT
    assert len(provider.calls) == 1
    sent: LLMRequest = provider.calls[0]
    # No shared context (codingrules 8.12): nothing in the sent request names a proposer or a
    # bee id; the rubric text and action summary are the only content carried.
    assert request.action.summary in (sent.system or "")
    assert request.rubric.text in (sent.system or "")
