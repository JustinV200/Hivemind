"""Tests for hivemind.wardens.judge: ModelJudgeReviewer.

Fits into the Hive:
    Mirrors src/hivemind/wardens/judge.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.judge for ModelJudgeReviewer, the class under test.
    - hivemind.supervision.capping.checks.judge for JudgeRequest/JudgeVerdict/JudgeOutcome.
    - hivemind.supervision.capping.errors for JudgeAnswerError, which review() raises in place of
      a propagated hivemind.llm.errors.MalformedOutputError.
"""

from __future__ import annotations

import json

import pytest
from builders.capping import make_judge_request
from builders.llm import make_bound

from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    DirectCallGate,
    FakeLLMProvider,
    LLMRequest,
    ProviderUnavailableError,
    StopReason,
    TextPart,
    Usage,
)
from hivemind.llm.models import LLMResponse
from hivemind.supervision.capping import JudgeAnswerError, JudgeOutcome
from hivemind.wardens.judge import ModelJudgeReviewer

_MODEL_ID = "test-model"
# NATIVE_SCHEMA_RETRIES(1)+1, JSON_MODE_RETRIES(2)+1, PROMPTED_JSON_RETRIES(3)+1 attempts before
# complete_structured gives up and raises MalformedOutputError -- 9 total, matching the real
# 2026-09-21 trail this module's own error-translation fixes (nine unparseable llm.call attempts).
_LADDER_EXHAUSTION_ATTEMPTS = 9


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


def _garbled_response() -> LLMResponse:
    """Build an LLMResponse whose text is not valid JSON, for every rung's parser to reject."""
    return LLMResponse(
        parts=(TextPart(text="not json"),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=10, output_tokens=5),
        model=_MODEL_ID,
    )


async def test_review_raises_judge_answer_error_when_the_ladder_cannot_parse_anything() -> None:
    """Nine unparseable llm.call attempts must become this layer's own JudgeAnswerError.

    2026-09-21: never hivemind.llm.errors.MalformedOutputError propagating unchanged --
    JudgeCheck.run (the only catcher) never imports hivemind.llm (codingrules section 4).
    """
    provider = FakeLLMProvider(name="judge-provider")
    provider.script(*([_garbled_response()] * _LADDER_EXHAUSTION_ATTEMPTS))
    bound = make_bound(slot=ModelSlot.JUDGE, provider=provider)
    reviewer = ModelJudgeReviewer(bound=bound, lane_for=lambda _tempo: DirectCallGate())
    request = make_judge_request()

    with pytest.raises(JudgeAnswerError, match="unparseable output"):
        await reviewer.review(request)


async def test_review_lets_provider_unavailable_error_propagate_unchanged() -> None:
    """An outage is Clustering's job (queen.cluster), not a per-proposal rejection.

    ModelJudgeReviewer.review must not catch ProviderUnavailableError.
    """
    provider = FakeLLMProvider(name="judge-provider")
    provider.set_outage(True)
    bound = make_bound(slot=ModelSlot.JUDGE, provider=provider)
    reviewer = ModelJudgeReviewer(bound=bound, lane_for=lambda _tempo: DirectCallGate())
    request = make_judge_request()

    with pytest.raises(ProviderUnavailableError):
        await reviewer.review(request)


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
