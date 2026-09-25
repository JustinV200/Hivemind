"""Contract suite for SubscriptionStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.entrance.push.store.protocol.SubscriptionStore contract and runs against both
    implementations that ship: hivemind.entrance.push.store.memory.MemorySubscriptionStore and
    hivemind.entrance.push.store.sqlite.SqliteSubscriptionStore (on a tmp_path SQLite file). A new
    implementation joins the fixture's params and must pass here before it is used anywhere else
    (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.store.protocol for the SubscriptionStore protocol under test.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from hivemind.common.sqlite import connect
from hivemind.entrance.auth import b64url_encode
from hivemind.entrance.push import (
    ChannelKind,
    MemorySubscriptionStore,
    SqliteSubscriptionStore,
    Subscription,
    SubscriptionExistsError,
    SubscriptionStore,
    WebPushKeys,
    new_subscription_id,
)
from waggle.clock import FakeClock
from waggle.ids import DeviceId, new_device_id

_STORE_KINDS = ("memory", "sqlite")
_HOOK = "https://hook.example.net/hive"  # A webhook endpoint.
_PUSH = "https://push.example.net/wpush/v2/abc"  # A push service endpoint.
_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # A question's id.


@pytest.fixture(params=_STORE_KINDS)
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> SubscriptionStore:
    """A SubscriptionStore of the parametrised kind."""
    if request.param == "memory":
        return MemorySubscriptionStore()
    return await SqliteSubscriptionStore.create(connect(tmp_path / "hive.sqlite3"), FakeClock())


def _keys() -> WebPushKeys:
    """Fresh Web Push keys, as a browser would register them."""
    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return WebPushKeys(p256dh=b64url_encode(point), auth=b64url_encode(bytes(range(16))))


def _subscription(
    clock: FakeClock,
    device_id: DeviceId,
    endpoint: str = _HOOK,
    channel: ChannelKind = ChannelKind.WEBHOOK,
) -> Subscription:
    """Build a subscription at the clock's time, then move the clock on a second."""
    subscription = Subscription(
        id=new_subscription_id(clock),
        device_id=device_id,
        channel=channel,
        endpoint=endpoint,
        keys=_keys() if channel is ChannelKind.WEB_PUSH else None,
        created_at=clock.now(),
    )
    clock.advance(1)
    return subscription


# ──────────────────────────────────────────────────────────────────────────────
# add / list
# ──────────────────────────────────────────────────────────────────────────────


async def test_add_then_list_returns_equal_subscriptions_oldest_first(
    store: SubscriptionStore,
) -> None:
    clock = FakeClock()
    device = new_device_id(clock)
    hook = _subscription(clock, device)
    push = _subscription(clock, device, _PUSH, ChannelKind.WEB_PUSH)
    other = _subscription(clock, new_device_id(clock))

    for subscription in (push, other, hook):
        await store.add(subscription)

    assert await store.list_for_device(device) == (hook, push)
    assert await store.list_all() == (hook, push, other)


async def test_list_for_a_device_with_nothing_is_empty(store: SubscriptionStore) -> None:
    assert await store.list_for_device(new_device_id(FakeClock())) == ()
    assert await store.list_all() == ()


async def test_add_refuses_a_duplicate_id(store: SubscriptionStore) -> None:
    clock = FakeClock()
    subscription = _subscription(clock, new_device_id(clock))
    await store.add(subscription)

    with pytest.raises(SubscriptionExistsError):
        await store.add(subscription.model_copy(update={"endpoint": "https://other.example.net/"}))


async def test_add_refuses_one_device_registering_one_endpoint_twice(
    store: SubscriptionStore,
) -> None:
    clock = FakeClock()
    device = new_device_id(clock)
    await store.add(_subscription(clock, device))

    with pytest.raises(SubscriptionExistsError):
        await store.add(_subscription(clock, device))


async def test_the_same_endpoint_may_serve_two_devices(store: SubscriptionStore) -> None:
    clock = FakeClock()

    await store.add(_subscription(clock, new_device_id(clock)))
    await store.add(_subscription(clock, new_device_id(clock)))

    assert len(await store.list_all()) == 2


# ──────────────────────────────────────────────────────────────────────────────
# delete / delete_for_device
# ──────────────────────────────────────────────────────────────────────────────


async def test_delete_removes_one_subscription_once(store: SubscriptionStore) -> None:
    clock = FakeClock()
    kept = _subscription(clock, new_device_id(clock))
    doomed = _subscription(clock, new_device_id(clock))
    await store.add(kept)
    await store.add(doomed)

    assert await store.delete(doomed.id) is True
    assert await store.delete(doomed.id) is False
    assert await store.list_all() == (kept,)


async def test_delete_for_device_removes_only_that_device(store: SubscriptionStore) -> None:
    clock = FakeClock()
    device = new_device_id(clock)
    first = _subscription(clock, device)
    second = _subscription(clock, device, _PUSH, ChannelKind.WEB_PUSH)
    other = _subscription(clock, new_device_id(clock))
    for subscription in (first, second, other):
        await store.add(subscription)

    removed = await store.delete_for_device(device)

    assert removed == (first.id, second.id)
    assert await store.list_all() == (other,)
    assert await store.delete_for_device(device) == ()


# ──────────────────────────────────────────────────────────────────────────────
# The delivery log
# ──────────────────────────────────────────────────────────────────────────────


async def test_recipients_are_the_subscriptions_recorded_for_a_ref(
    store: SubscriptionStore,
) -> None:
    clock = FakeClock()
    first = _subscription(clock, new_device_id(clock))
    second = _subscription(clock, new_device_id(clock))
    missed = _subscription(clock, new_device_id(clock))
    for subscription in (first, second, missed):
        await store.add(subscription)

    await store.record_delivery(_REF, [second.id, first.id], clock.now())
    await store.record_delivery(_REF, [first.id], clock.now())

    assert await store.recipients(_REF) == (first, second)
    assert await store.recipients("msg_other") == ()


async def test_record_delivery_skips_an_id_no_longer_stored(store: SubscriptionStore) -> None:
    clock = FakeClock()
    stored = _subscription(clock, new_device_id(clock))
    await store.add(stored)
    unknown = new_subscription_id(clock)

    await store.record_delivery(_REF, [unknown, stored.id], clock.now())

    assert await store.recipients(_REF) == (stored,)


async def test_deleting_a_subscription_removes_it_from_every_ref(store: SubscriptionStore) -> None:
    clock = FakeClock()
    device = new_device_id(clock)
    kept = _subscription(clock, new_device_id(clock))
    doomed = _subscription(clock, device)
    await store.add(kept)
    await store.add(doomed)
    await store.record_delivery(_REF, [kept.id, doomed.id], clock.now())

    await store.delete_for_device(device)

    assert await store.recipients(_REF) == (kept,)


async def test_forget_ref_clears_that_ref_only(store: SubscriptionStore) -> None:
    clock = FakeClock()
    subscription = _subscription(clock, new_device_id(clock))
    await store.add(subscription)
    await store.record_delivery(_REF, [subscription.id], clock.now())
    await store.record_delivery("alarm_01J8ZQ7X9K3M2N4P5Q6R7S8T9V", [subscription.id], clock.now())

    forgotten = await store.forget_ref(_REF)

    assert forgotten == 1
    assert await store.recipients(_REF) == ()
    assert await store.recipients("alarm_01J8ZQ7X9K3M2N4P5Q6R7S8T9V") == (subscription,)
    assert await store.forget_ref(_REF) == 0


async def test_a_web_push_subscription_keeps_its_keys(store: SubscriptionStore) -> None:
    clock = FakeClock()
    subscription = _subscription(clock, new_device_id(clock), _PUSH, ChannelKind.WEB_PUSH)

    await store.add(subscription)

    (stored,) = await store.list_all()
    assert stored.keys == subscription.keys
