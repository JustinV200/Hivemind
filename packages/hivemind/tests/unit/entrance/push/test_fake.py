"""Tests for hivemind.entrance.push.fake: FakePush records deliveries and answers as scripted.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.fake for the module under test.
"""

from __future__ import annotations

from unit.entrance.push.support import SteppingClock, webhook_subscription

from hivemind.entrance.push import DeliveryOutcome, FakePush, NoticeKind, PushNotice

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question every notice here points at.


async def test_fake_records_every_delivery_and_answers_delivered_by_default() -> None:
    clock = SteppingClock()
    fake = FakePush()
    subscription = webhook_subscription(clock)
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock)

    outcome = await fake.deliver(notice, subscription)

    assert outcome is DeliveryOutcome.DELIVERED
    assert fake.deliveries == [(notice, subscription)]


async def test_fake_answers_the_scripted_outcome_per_subscription() -> None:
    clock = SteppingClock()
    fake = FakePush(default=DeliveryOutcome.RETRY_LATER)
    gone, other = webhook_subscription(clock), webhook_subscription(clock)
    fake.answer(gone.id, DeliveryOutcome.GONE)
    notice = PushNotice.mint(NoticeKind.ALARM_WAITING, _REF, clock)

    first = await fake.deliver(notice, gone)
    second = await fake.deliver(notice, other)

    assert (first, second) == (DeliveryOutcome.GONE, DeliveryOutcome.RETRY_LATER)
    assert len(fake.deliveries) == 2


async def test_received_filters_the_record_by_kind() -> None:
    clock = SteppingClock()
    fake = FakePush()
    subscription = webhook_subscription(clock)
    await fake.deliver(PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock), subscription)
    await fake.deliver(PushNotice.mint(NoticeKind.WITHDRAWN, _REF, clock), subscription)

    assert fake.received(NoticeKind.WITHDRAWN) == [(_REF, subscription.id)]
    assert fake.received(NoticeKind.REPLY_WAITING) == []
