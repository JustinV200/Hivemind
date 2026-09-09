"""Unit tests for hivemind.workers.context: GrantSlice, QuestionChannel and WorkerContext."""

from __future__ import annotations

import pytest
from builders.workers import FakeAsker, make_context, make_grant_slice
from pydantic import ValidationError

from hivemind.workers.context import GrantSlice, WorkerContext
from waggle.clock import FakeClock
from waggle.ids import new_message_id, new_task_id, new_worker_id
from waggle.messages.labels import HoneyClearance
from waggle.messages.supervision import Answer, AnswerSource, Question


def test_make_context_builds_a_valid_worker_context() -> None:
    ctx = make_context()

    assert isinstance(ctx, WorkerContext)
    assert ctx.handoff_threshold == pytest.approx(0.66)


def test_worker_context_is_frozen() -> None:
    ctx = make_context()

    with pytest.raises(AttributeError):
        ctx.worker_id = ctx.worker_id  # type: ignore[misc]


def test_grant_slice_carries_no_model_shaped_field() -> None:
    slice_ = make_grant_slice()

    assert not hasattr(slice_, "provider")
    assert not hasattr(slice_, "model")
    assert slice_.allowed_bindings == ("worker",)


def test_grant_slice_rejects_negative_budgets() -> None:
    with pytest.raises(ValidationError):
        make_grant_slice(spend_budget_usd=-1.0)
    with pytest.raises(ValidationError):
        make_grant_slice(token_budget=-1)


def test_grant_slice_is_frozen_and_forbids_extras() -> None:
    with pytest.raises(ValidationError):
        GrantSlice(
            grant_id=make_grant_slice().grant_id,
            spend_budget_usd=1.0,
            token_budget=1,
            allowed_bindings=(),
            extra="nope",  # type: ignore[call-arg]
        )


async def test_fake_asker_records_the_question_and_returns_the_scripted_answer() -> None:
    clock = FakeClock()
    asker = FakeAsker()
    question = Question(
        question_id=new_message_id(clock),
        task_id=new_task_id(clock),
        asked_by=new_worker_id(clock),
        text="Which environment?",
        options=(),
        clearance=HoneyClearance.C1,
        asked_at=clock.now(),
    )
    answer = Answer(
        question_id=question.question_id,
        task_id=question.task_id,
        text="staging",
        chosen_option=None,
        source=AnswerSource.WARDEN,
        clearance=HoneyClearance.C1,
    )
    asker.script(answer)

    received = await asker.ask(question)

    assert received is answer
    assert asker.questions == [question]
