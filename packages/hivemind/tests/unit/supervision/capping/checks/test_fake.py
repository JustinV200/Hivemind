"""Unit tests for hivemind.supervision.capping.checks.fake: FakeJudgeReviewer."""

from __future__ import annotations

import pytest
from builders.capping import make_judge_request, make_judge_verdict

from hivemind.supervision.capping.checks.fake import FakeJudgeReviewer
from hivemind.supervision.capping.checks.judge import JudgeOutcome
from hivemind.supervision.capping.errors import JudgeUnavailableError
from hivemind.supervision.capping.tiers import RiskTier


async def test_fake_judge_reviewer_answers_from_the_scripted_queue_in_order() -> None:
    first = make_judge_verdict(JudgeOutcome.APPROVE)
    second = make_judge_verdict(JudgeOutcome.REJECT)
    reviewer = FakeJudgeReviewer(first, second)
    request = make_judge_request()

    assert await reviewer.review(request) is first
    assert await reviewer.review(request) is second


async def test_fake_judge_reviewer_script_appends_to_the_existing_queue() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict(JudgeOutcome.APPROVE))
    later = make_judge_verdict(JudgeOutcome.REJECT)
    reviewer.script(later)
    request = make_judge_request()

    await reviewer.review(request)  # Consumes the constructor's verdict.

    assert await reviewer.review(request) is later


async def test_fake_judge_reviewer_records_every_request_in_order() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict(), make_judge_verdict())
    first_request = make_judge_request(risk_tier=RiskTier.SCRATCH_WRITE)
    second_request = make_judge_request(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)

    await reviewer.review(first_request)
    await reviewer.review(second_request)

    assert reviewer.calls == [first_request, second_request]


async def test_fake_judge_reviewer_raises_on_an_empty_queue() -> None:
    reviewer = FakeJudgeReviewer()

    with pytest.raises(JudgeUnavailableError):
        await reviewer.review(make_judge_request())
