"""Unit tests for hivemind.workers.tools.ask: ask, a blocking Question round-trip.

Roadmap step 10.3: asking the human is the `question_routing` point, so a Worker whose set lacks
`question:human` is refused (a `guard.denied` row) and no Question ever leaves it.
"""

from __future__ import annotations

from builders.workers import FakeAsker, make_assignment, make_context

from hivemind.guard import CapabilitySet
from hivemind.pheromone import TrailQuery
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


async def test_ask_refuses_the_capping_gates_own_leave_options() -> None:
    # The Queen remembers a human's answer to a Question with these options (roadmap 5.0d), so a
    # model wording one itself would be asking for approvals the human never saw.
    invocation, asker = _make_invocation_and_asker()

    result = await ask(
        invocation,
        {"text": "Shall I continue?", "options": ["Keep", " keep for this whole goal ", "DISCARD"]},
    )

    assert "reserved" in result
    assert asker.questions == []


async def test_ask_without_question_human_is_refused_and_asks_nothing() -> None:
    asker = FakeAsker()
    ctx = make_context(asker=asker, capabilities=CapabilitySet.parse("tool:*"))
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await ask(invocation, {"text": "Which environment?"})

    assert result.startswith("refused by the Guard (guard.not_held)")
    assert asker.questions == []
    [denial] = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "question_routing"
    assert denial.payload["capability"] == "question:human"
