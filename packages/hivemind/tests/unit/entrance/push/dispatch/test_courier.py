"""Tests for hivemind.entrance.push.dispatch.courier: concurrent delivery and settling outcomes.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/dispatch/courier.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.dispatch.courier for the module under test.
"""

from __future__ import annotations

import asyncio

from unit.entrance.push.support import SteppingClock, webhook_subscription

from hivemind.entrance.push import (
    ChannelKind,
    DeliveryOutcome,
    FakePush,
    LivePush,
    MemorySubscriptionStore,
    NoticeKind,
    PushChannels,
    PushConfigError,
    PushNotice,
    Subscription,
)
from hivemind.entrance.push.dispatch import Courier

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question every notice here points at.


class _RaisingPush:
    """A channel that breaks its contract with a typed PushError."""

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Raise instead of answering; see PushChannel.deliver."""
        raise PushConfigError("entrance.vapid", "a valid key")


class _SlowPush:
    """A channel that answers only once released, to prove deliveries run side by side."""

    def __init__(self) -> None:
        """Start held."""
        self.release = asyncio.Event()

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Wait for the release, then deliver; see PushChannel.deliver."""
        await self.release.wait()
        return DeliveryOutcome.DELIVERED


async def test_a_channel_raising_a_push_error_is_refused_and_the_other_still_delivers() -> None:
    clock = SteppingClock()
    fine = FakePush()
    channels = PushChannels(
        live=LivePush(), stored={ChannelKind.WEBHOOK: _RaisingPush(), ChannelKind.WEB_PUSH: fine}
    )
    courier = Courier(channels, MemorySubscriptionStore(), clock)
    broken = webhook_subscription(clock)
    working = broken.model_copy(update={"id": "sub_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"})
    working = working.model_copy(update={"channel": ChannelKind.WEB_PUSH})
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock)

    report = await courier.deliver(notice, [broken, working], frozenset())

    assert report.outcomes[broken.id] is DeliveryOutcome.REFUSED
    assert report.outcomes[working.id] is DeliveryOutcome.DELIVERED
    assert len(fine.deliveries) == 1


async def test_deliveries_run_side_by_side_not_one_after_another() -> None:
    clock = SteppingClock()
    slow = _SlowPush()
    fast = FakePush()
    channels = PushChannels(
        live=LivePush(), stored={ChannelKind.WEBHOOK: slow, ChannelKind.WEB_PUSH: fast}
    )
    courier = Courier(channels, MemorySubscriptionStore(), clock)
    held = webhook_subscription(clock)
    quick = held.model_copy(
        update={"id": "sub_01J8ZQ7X9K3M2N4P5Q6R7S8T9V", "channel": ChannelKind.WEB_PUSH}
    )
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock)

    async with asyncio.TaskGroup() as group:
        delivering = group.create_task(courier.deliver(notice, [held, quick], frozenset()))
        # The quick channel is reached while the slow one is still held.
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(fast.deliveries) == 1
        slow.release.set()

    assert delivering.result().delivered == frozenset({held.id, quick.id})


async def test_settle_records_only_originals_and_deletes_what_is_gone() -> None:
    clock = SteppingClock()
    store = MemorySubscriptionStore()
    fake = FakePush()
    courier = Courier(
        PushChannels(live=LivePush(), stored={ChannelKind.WEBHOOK: fake}), store, clock
    )
    kept, gone = webhook_subscription(clock), webhook_subscription(clock)
    await store.add(kept)
    await store.add(gone)
    fake.answer(gone.id, DeliveryOutcome.GONE)
    withdrawal = PushNotice.mint(NoticeKind.WITHDRAWN, _REF, clock)

    report = await courier.deliver(withdrawal, [kept, gone], frozenset())
    await courier.settle(report, [kept, gone], record=False)

    assert await store.list_all() == (kept,)
    assert await store.recipients(_REF) == ()
