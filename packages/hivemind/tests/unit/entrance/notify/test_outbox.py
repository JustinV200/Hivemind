"""Test hivemind.entrance.notify.outbox: notices leave the caller's path, in order per ref.

Fits into the Hive:
    Mirrors src/hivemind/entrance/notify/outbox.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio

from unit.entrance.notify.support import running_outbox
from unit.entrance.push.support import SteppingClock, approved_device

from hivemind.entrance.push import DeliveryOutcome, FakePush, NoticeKind, PushNotice, Subscription

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question every notice here points at.
_ANSWERER = ("entrance:push", "entrance:answer")  # A device that hears questions.


class _GatedPush(FakePush):
    """A FakePush that holds every question notice until the test opens the gate."""

    def __init__(self) -> None:
        """Start with the gate shut."""
        super().__init__()
        self.gate = asyncio.Event()

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Wait for the gate on a question, then record it; see PushChannel.deliver."""
        if notice.kind is NoticeKind.QUESTION_WAITING:
            await self.gate.wait()
        return await super().deliver(notice, subscription)


async def test_push_only_queues_and_the_running_outbox_delivers() -> None:
    device = approved_device(SteppingClock(), *_ANSWERER)
    async with running_outbox([device]) as rig:
        queued = rig.outbox.push(NoticeKind.QUESTION_WAITING, _REF)

        await rig.outbox.join()

    assert queued
    assert [ref for ref, _ in rig.channel.received(NoticeKind.QUESTION_WAITING)] == [_REF]


async def test_a_withdrawal_never_overtakes_its_notice() -> None:
    device = approved_device(SteppingClock(), *_ANSWERER)
    gated = _GatedPush()
    async with running_outbox([device], channel=gated) as rig:
        rig.outbox.push(NoticeKind.QUESTION_WAITING, _REF)
        rig.outbox.withdraw(_REF)
        # Give the withdrawal every chance to run first; the ref's order must hold it back.
        for _ in range(20):
            await asyncio.sleep(0)
        held_back = list(gated.deliveries)
        gated.gate.set()

        await rig.outbox.join()

    assert held_back == []
    assert [notice.kind for notice, _ in gated.deliveries] == [
        NoticeKind.QUESTION_WAITING,
        NoticeKind.WITHDRAWN,
    ]


async def test_a_full_outbox_drops_the_new_job_instead_of_waiting() -> None:
    device = approved_device(SteppingClock(), *_ANSWERER)
    async with running_outbox([device], capacity=1, start=False) as rig:
        first = rig.outbox.push(NoticeKind.QUESTION_WAITING, _REF)
        second = rig.outbox.withdraw(_REF)

    assert (first, second) == (True, False)


async def test_the_audience_is_decided_when_the_notice_is_delivered() -> None:
    clock = SteppingClock()
    answerer = approved_device(clock, *_ANSWERER)
    listener_only = approved_device(clock, "entrance:push")
    async with running_outbox([answerer, listener_only]) as rig:
        rig.outbox.push(NoticeKind.QUESTION_WAITING, _REF)

        await rig.outbox.join()

    assert [subscription.device_id for _, subscription in rig.channel.deliveries] == [answerer.id]
