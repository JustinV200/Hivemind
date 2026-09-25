"""Test hivemind.entrance.notify.relay: the Queen's channel before and after the Entrance exists.

Fits into the Hive:
    Mirrors src/hivemind/entrance/notify/relay.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.human import RecordingHumanChannel, make_goal_request

from hivemind.brood_chamber import QuestionStatus
from hivemind.entrance.notify import HumanChannelRelay
from waggle.clock import FakeClock


async def test_a_call_before_binding_is_dropped_and_later_ones_are_forwarded() -> None:
    relay = HumanChannelRelay()
    target = RecordingHumanChannel()
    request = make_goal_request(FakeClock())

    await relay.goal_request_held(request)
    relay.bind(target)
    await relay.goal_request_held(request)
    await relay.question_closed("msg_1", QuestionStatus.ANSWERED)
    await relay.alarm_acknowledged("alarm_1")

    assert target.names() == ["goal_request_held", "question_closed", "alarm_acknowledged"]
