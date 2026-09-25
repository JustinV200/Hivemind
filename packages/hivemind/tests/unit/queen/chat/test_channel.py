"""Tests for hivemind.queen.chat.channel: the no-op default answers every HumanChannel call.

Fits into the Hive:
    Mirrors src/hivemind/queen/chat/channel.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.chat.channel for HumanChannel and NullHumanChannel.
"""

from __future__ import annotations

import asyncio
import inspect

from builders.human import RecordingHumanChannel

from hivemind.brood_chamber import QuestionStatus
from hivemind.queen.chat import HumanChannel, NullHumanChannel


def _protocol_methods() -> set[str]:
    """Every method a HumanChannel must answer, read off the protocol itself."""
    return {
        name
        for name, member in inspect.getmembers(HumanChannel, inspect.isfunction)
        if not name.startswith("_")
    }


def test_the_protocol_names_every_moment_the_human_is_told_about() -> None:
    assert _protocol_methods() == {
        "replied",
        "question_asked",
        "question_closed",
        "alarm_raised",
        "alarm_acknowledged",
        "goal_request_held",
        "goal_request_planned",
        "goal_request_refused",
        "goal_finished",
    }


def test_the_null_channel_and_the_test_recorder_answer_every_method() -> None:
    for implementation in (NullHumanChannel, RecordingHumanChannel):
        missing = {
            name
            for name in _protocol_methods()
            if not inspect.iscoroutinefunction(getattr(implementation, name, None))
        }
        assert missing == set(), f"{implementation.__name__} lacks {sorted(missing)}"


async def test_the_null_channel_returns_at_once_and_tells_nobody() -> None:
    channel: HumanChannel = NullHumanChannel()

    # A channel that blocked, or tried to reach anyone, would trip this bound.
    async with asyncio.timeout(1):
        await channel.alarm_acknowledged("alarm_x")
        await channel.question_closed("question_x", QuestionStatus.ANSWERED)
