"""Test hivemind.entrance.notify.human: the Queen's moments become content-free push notices.

Fits into the Hive:
    Mirrors src/hivemind/entrance/notify/human.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.human import make_goal_request
from unit.entrance.notify.support import running_outbox
from unit.entrance.push.support import SteppingClock, approved_device

from hivemind.brood_chamber import QuestionStatus
from hivemind.entrance.notify import PushHumanChannel
from hivemind.entrance.push import NoticeKind
from hivemind.queen.chat import ChatAuthor, ChatEntry, ChatKind
from waggle.ids import new_alarm_id

_EVERYTHING = ("entrance:push", "entrance:answer", "entrance:submit", "observe")


def _question(clock: SteppingClock) -> ChatEntry:
    """A question line the Queen appended; its ref is the question id."""
    return ChatEntry(
        seq=1,
        id="chat_01J8ZQ7X9K3M2N4P5Q6R7S8T9V",
        at=clock.now(),
        author=ChatAuthor.QUEEN,
        kind=ChatKind.QUESTION,
        text="Staging or prod?",
        ref="msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9W",
    )


async def test_a_question_is_pushed_then_withdrawn_when_it_closes() -> None:
    clock = SteppingClock()
    device = approved_device(clock, *_EVERYTHING)
    entry = _question(clock)
    async with running_outbox([device]) as rig:
        channel = PushHumanChannel(rig.outbox)

        await channel.question_asked(entry)
        await channel.question_closed(entry.ref or "", QuestionStatus.ANSWERED)
        await rig.outbox.join()

    assert [(notice.kind, notice.ref) for notice, _ in rig.channel.deliveries] == [
        (NoticeKind.QUESTION_WAITING, entry.ref),
        (NoticeKind.WITHDRAWN, entry.ref),
    ]
    assert all(entry.text not in notice.model_dump_json() for notice, _ in rig.channel.deliveries)


async def test_an_alarm_reaches_every_device_and_its_acknowledgement_withdraws_it() -> None:
    clock = SteppingClock()
    # An Alarm is every device's business, not only the ones that may answer or read the chat.
    answerer = approved_device(clock, *_EVERYTHING)
    listener = approved_device(clock, "entrance:push")
    entry = ChatEntry(
        seq=2,
        id="chat_01J8ZQ7X9K3M2N4P5Q6R7S8T9X",
        at=clock.now(),
        author=ChatAuthor.QUEEN,
        kind=ChatKind.ALARM,
        text="The Hive Stand's disk is nearly full.",
        ref=new_alarm_id(clock),
    )
    async with running_outbox([answerer, listener]) as rig:
        channel = PushHumanChannel(rig.outbox)

        await channel.alarm_raised(entry)
        await channel.alarm_acknowledged(entry.ref or "")
        await rig.outbox.join()

    told = {(n.kind, n.ref, s.device_id) for n, s in rig.channel.deliveries}
    assert told == {
        (kind, entry.ref, device.id)
        for kind in (NoticeKind.ALARM_WAITING, NoticeKind.WITHDRAWN)
        for device in (answerer, listener)
    }
    assert all(entry.text not in notice.model_dump_json() for notice, _ in rig.channel.deliveries)


async def test_a_finished_goal_is_told_to_its_submitter_alone() -> None:
    clock = SteppingClock()
    submitter = approved_device(clock, *_EVERYTHING)
    bystander = approved_device(clock, *_EVERYTHING)
    request = make_goal_request(clock, device_id=submitter.id)
    async with running_outbox([submitter, bystander]) as rig:
        await PushHumanChannel(rig.outbox).goal_finished(request)
        await rig.outbox.join()

    [(notice, subscription)] = rig.channel.deliveries
    assert (notice.kind, notice.ref) == (NoticeKind.GOAL_COMPLETED, request.id)
    assert subscription.device_id == submitter.id


async def test_a_held_goal_waits_as_a_question_and_its_plan_withdraws_it() -> None:
    clock = SteppingClock()
    device = approved_device(clock, *_EVERYTHING)
    request = make_goal_request(clock, device_id=device.id)
    async with running_outbox([device]) as rig:
        channel = PushHumanChannel(rig.outbox)

        await channel.goal_request_held(request)
        await channel.goal_request_planned(request)
        await rig.outbox.join()

    assert [(notice.kind, notice.ref) for notice, _ in rig.channel.deliveries] == [
        (NoticeKind.QUESTION_WAITING, request.id),
        (NoticeKind.WITHDRAWN, request.id),
    ]
