"""Tests for hivemind.honey_store.lowering.fake: FakeClearanceJudge, the scripted clearance judge.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/lowering/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.lowering.fake for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.cell import HoneyClearance
from hivemind.honey_store.errors import ClearanceJudgeAnswerError
from hivemind.honey_store.lowering.fake import SCRIPTED_REASON, FakeClearanceJudge
from hivemind.honey_store.lowering.judge import ClearanceJudge
from hivemind.honey_store.lowering.models import ClearanceJudgeRequest, ClearanceOutcome
from waggle.messages.honey import NectarKind


def _request(text: str = "A build log.") -> ClearanceJudgeRequest:
    """A small, well-formed judge request under a test rubric."""
    return ClearanceJudgeRequest(
        text=text,
        title="Build",
        kind=NectarKind.FINDING,
        media_type="text/plain",
        target=HoneyClearance.C1,
        rubric_id="test-rubric/1",
    )


async def test_fake_answers_in_script_order_and_records_every_request() -> None:
    judge = FakeClearanceJudge(ClearanceOutcome.APPROVE)
    judge.script(ClearanceOutcome.REJECT)

    first = await judge.judge(_request("one"))
    second = await judge.judge(_request("two"))

    assert (first.outcome, second.outcome) == (ClearanceOutcome.APPROVE, ClearanceOutcome.REJECT)
    assert first.reasons == (SCRIPTED_REASON,)
    assert first.rubric_id == "test-rubric/1"
    assert [request.text for request in judge.requests] == ["one", "two"]


async def test_fake_raises_a_scripted_answer_failure() -> None:
    failure = ClearanceJudgeAnswerError("MalformedOutputError", "scripted")
    judge = FakeClearanceJudge(failure)

    with pytest.raises(ClearanceJudgeAnswerError) as caught:
        await judge.judge(_request())

    assert caught.value is failure


async def test_fake_answers_its_default_once_the_script_runs_out() -> None:
    judge = FakeClearanceJudge(default=ClearanceOutcome.REJECT)

    verdict = await judge.judge(_request())

    assert verdict.outcome is ClearanceOutcome.REJECT


async def test_fake_with_no_default_cannot_answer_once_the_script_runs_out() -> None:
    judge = FakeClearanceJudge()

    with pytest.raises(ClearanceJudgeAnswerError, match="ran out"):
        await judge.judge(_request())


def test_fake_satisfies_the_clearance_judge_protocol() -> None:
    judge: ClearanceJudge = FakeClearanceJudge()

    assert judge is not None
