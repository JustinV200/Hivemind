"""Tests for hivemind.queen.chat.post: every chat write, its event, and its HumanChannel call.

Fits into the Hive:
    Mirrors src/hivemind/queen/chat/post.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.chat.post for the functions under test.
"""

from __future__ import annotations

import json

from builders.human import RecordingHumanChannel, make_goal_request
from builders.queen import make_queen_deps
from builders.supervision import make_alarm
from builders.tasks import make_question

from hivemind.pheromone import TrailQuery
from hivemind.queen.chat import (
    ECHO_PREFIX,
    ChatAuthor,
    ChatKind,
    ChatQuery,
    echo_goal,
    post_alarm,
    post_message,
    post_notice,
    post_question,
    post_reply,
    resolve_alarm,
)
from hivemind.queen.deps import QueenDeps
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.intake import GoalRequestState, GoalSource, hold, receive
from waggle.ids import new_device_id, new_task_id

_WORDS = "My sister Beatrice arrives on Tuesday."  # Personal words that must never reach the trail.


def _deps() -> tuple[QueenDeps, RecordingHumanChannel]:
    channel = RecordingHumanChannel()
    deps, _link, _end = make_queen_deps(human_channel=channel)
    return deps, channel


async def test_post_message_appends_the_humans_line_and_records_that_it_arrived() -> None:
    deps, channel = _deps()
    device, task = new_device_id(deps.clock), new_task_id(deps.clock)

    line = await post_message(deps, _WORDS, device, task)

    assert (line.author, line.kind, line.text, line.device_id) == (
        ChatAuthor.HUMAN,
        ChatKind.MESSAGE,
        _WORDS,
        device,
    )
    [event] = await deps.trail.query(TrailQuery())
    assert event.kind == "queen.human_message_received"
    assert event.subject_id == task
    assert event.payload["chat_entry_id"] == line.id
    assert "Beatrice" not in json.dumps(event.payload)
    assert channel.calls == []  # The human's own words are not pushed back to the human.


async def test_post_reply_records_queen_replied_and_tells_the_channel() -> None:
    deps, channel = _deps()
    asked = await post_message(deps, _WORDS, new_device_id(deps.clock), None)

    reply = await post_reply(deps, "I will remember Tuesday.", ref=asked.id, task_id=None)

    assert (reply.author, reply.kind, reply.ref) == (ChatAuthor.QUEEN, ChatKind.REPLY, asked.id)
    [event] = await deps.trail.query(TrailQuery(kind="queen.replied"))
    assert event.payload == {"chat_entry_id": reply.id, "answering": asked.id}
    assert channel.calls == [("replied", reply)]


async def test_post_notice_appends_without_an_event_or_a_channel_call() -> None:
    deps, channel = _deps()

    notice = await post_notice(deps, "Noted.", ref=None, task_id=None)

    assert notice.kind is ChatKind.NOTICE
    assert await deps.trail.query(TrailQuery()) == ()
    assert channel.calls == []


async def test_post_question_lists_its_options_and_tells_the_channel() -> None:
    deps, channel = _deps()
    question = make_question(deps.clock, options=("staging", "prod"))

    line = await post_question(deps, question)

    assert line.kind is ChatKind.QUESTION
    assert line.ref == question.id
    assert line.task_id == question.task_id
    assert line.text.splitlines()[1:] == ["- staging", "- prod"]
    assert channel.calls == [("question_asked", line)]


async def test_post_alarm_names_the_alarm_and_tells_the_channel() -> None:
    deps, channel = _deps()
    alarm = make_alarm(clock=deps.clock)

    line = await post_alarm(deps, alarm)

    assert line.kind is ChatKind.ALARM
    assert line.ref == alarm.id
    assert alarm.detail in line.text
    assert channel.calls == [("alarm_raised", line)]
    assert [entry.id for entry in await deps.chat.read(ChatQuery())] == [line.id]


async def test_resolve_alarm_resolves_it_once_records_it_and_tells_the_channel() -> None:
    deps, channel = _deps()
    inbox = HumanInbox()
    alarm = make_alarm(clock=deps.clock)
    inbox.add_alarm(alarm)

    first = await resolve_alarm(deps, inbox, alarm.id)
    second = await resolve_alarm(deps, inbox, alarm.id)

    assert (first, second) == (True, False)
    assert inbox.alarms == ()
    [event] = await deps.trail.query(TrailQuery(kind="alarm.resolved"))
    assert event.subject_id == alarm.id
    assert event.payload["by"] == "human"
    assert channel.calls == [("alarm_acknowledged", alarm.id)]


async def test_echo_goal_posts_the_echo_then_holds_the_request_and_tells_the_channel() -> None:
    deps, channel = _deps()
    spoken = make_goal_request(deps.clock, source=GoalSource.SPOKEN, needs_confirmation=True)
    await receive(deps, spoken)

    held = await echo_goal(deps, spoken)

    assert held is not None and held.state is GoalRequestState.AWAITING_CONFIRMATION
    [echo] = await deps.chat.read(ChatQuery())
    assert (echo.kind, echo.ref, echo.text) == (
        ChatKind.NOTICE,
        spoken.id,
        f"{ECHO_PREFIX}{spoken.text}",
    )
    assert channel.calls == [("goal_request_held", held)]
    kinds = [event.kind for event in await deps.trail.query(TrailQuery())]
    assert kinds == ["queen.goal_request_received", "queen.goal_request_held"]


async def test_echo_goal_leaves_a_request_that_moved_on_and_tells_nobody() -> None:
    deps, channel = _deps()
    spoken = make_goal_request(deps.clock, source=GoalSource.SPOKEN, needs_confirmation=True)
    await hold(deps, await receive(deps, spoken))

    held = await echo_goal(deps, spoken)

    assert held is None  # Already held by someone else: that state stands.
    assert (await deps.goal_requests.get(spoken.id)).state is GoalRequestState.AWAITING_CONFIRMATION
    assert channel.calls == []
