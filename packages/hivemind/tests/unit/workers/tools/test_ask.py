"""Unit tests for hivemind.workers.tools.ask: ask, a blocking Question round-trip."""

from __future__ import annotations

from builders.workers import FakeAsker, make_assignment, make_context

from hivemind.workers.tools.ask import ask
from hivemind.workers.tools.registry import ToolInvocation
from waggle.clock import FakeClock
from waggle.ids import new_message_id
from waggle.messages.labels import HoneyClearance
from waggle.messages.supervision import Answer, AnswerSource


def _make_invocation_and_asker() -> tuple[ToolInvocation, FakeAsker]:
    asker = FakeAsker()
    ctx = make_context(asker=asker)
    return ToolInvocation(ctx=ctx, assignment=make_assignment()), asker


async def test_ask_round_trips_through_a_scripted_question_channel() -> None:
    invocation, asker = _make_invocation_and_asker()
    asker.script(
        Answer(
            question_id=new_message_id(FakeClock()),
            task_id=invocation.assignment.task_id,
            text="staging",
            chosen_option=None,
            source=AnswerSource.WARDEN,
            clearance=HoneyClearance.C1,
        )
    )

    result = await ask(invocation, {"text": "Which environment?"})

    assert result == "staging"
    assert len(asker.questions) == 1
    assert asker.questions[0].text == "Which environment?"


async def test_ask_names_the_chosen_option_when_one_was_offered() -> None:
    invocation, asker = _make_invocation_and_asker()
    asker.script(
        Answer(
            question_id=new_message_id(FakeClock()),
            task_id=invocation.assignment.task_id,
            text="picked the second one",
            chosen_option=1,
            source=AnswerSource.WARDEN,
            clearance=HoneyClearance.C1,
        )
    )

    result = await ask(invocation, {"text": "Pick one.", "options": ["red", "blue"]})

    assert "picked the second one" in result
    assert "blue" in result


async def test_ask_rejects_an_empty_text() -> None:
    invocation, _ = _make_invocation_and_asker()

    result = await ask(invocation, {"text": ""})

    assert result == "text must be a non-empty string."
