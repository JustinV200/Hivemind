"""Tests for hivemind.entrance.push.dispatch.dispatcher: register, push, withdraw, offboard.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/dispatch/dispatcher.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.dispatch.dispatcher for the module under test.
    - test_end_to_end.py beside this module for the same flows over the real channels.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from builders.entrance import make_device
from unit.entrance.push.support import (
    HOOK_URL,
    PUSH_URL,
    SteppingClock,
    UserAgent,
    approved_device,
    make_guard,
)

from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice
from hivemind.entrance.push import (
    Admission,
    ChannelKind,
    DeliveryOutcome,
    DestinationRefusedError,
    FakePush,
    LivePush,
    MemorySubscriptionStore,
    NoticeKind,
    PushChannels,
    PushDispatcher,
    PushNotice,
    PushRegistrationRefusedError,
    Subscription,
    audience_for,
)
from hivemind.entrance.push.dispatch import MAX_SUBSCRIPTIONS_PER_DEVICE
from hivemind.manifest.schema import EntrancePushSection

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question every notice here points at.
_ANSWERER = ("entrance:push", "entrance:answer")  # A device that hears questions.


@dataclass
class _Rig:
    """A dispatcher over fake channels and an in-memory store, and the handles to inspect."""

    dispatcher: PushDispatcher
    store: MemorySubscriptionStore
    webhook: FakePush
    web_push: FakePush
    live: LivePush
    clock: SteppingClock


def _rig(webhook: FakePush | None = None, *, web_push_on: bool = True) -> _Rig:
    """Build a dispatcher whose stored channels are FakePush and whose live hub is real."""
    clock = SteppingClock()
    store = MemorySubscriptionStore()
    hooks, pushes, live = webhook or FakePush(), FakePush(), LivePush()
    stored = (
        {ChannelKind.WEBHOOK: hooks, ChannelKind.WEB_PUSH: pushes}
        if web_push_on
        else {ChannelKind.WEBHOOK: hooks}
    )
    admission = Admission(EntrancePushSection(), make_guard())
    dispatcher = PushDispatcher(PushChannels(live=live, stored=stored), store, admission, clock)
    return _Rig(dispatcher, store, hooks, pushes, live, clock)


async def _question(rig: _Rig, *devices: EnrolledDevice) -> PushNotice:
    """Push a question notice to the question audience of ``devices``; return the notice."""
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, rig.clock)
    await rig.dispatcher.push(notice, audience_for(NoticeKind.QUESTION_WAITING, devices))
    return notice


# ──────────────────────────────────────────────────────────────────────────────
# register
# ──────────────────────────────────────────────────────────────────────────────


async def test_register_stores_an_admitted_subscription() -> None:
    rig = _rig()
    device = approved_device(rig.clock)

    subscription = await rig.dispatcher.register(device, ChannelKind.WEBHOOK, HOOK_URL)

    assert subscription.device_id == device.id
    assert subscription.created_at == rig.clock.now()
    assert await rig.store.list_for_device(device.id) == (subscription,)


async def test_register_the_same_endpoint_again_is_the_same_subscription() -> None:
    rig = _rig()
    device, agent = approved_device(rig.clock), UserAgent()

    first = await rig.dispatcher.register(device, ChannelKind.WEB_PUSH, PUSH_URL, agent.keys)
    again = await rig.dispatcher.register(device, ChannelKind.WEB_PUSH, PUSH_URL, agent.keys)

    assert again == first
    assert len(await rig.store.list_all()) == 1


async def test_register_the_same_endpoint_with_new_keys_replaces_it() -> None:
    rig = _rig()
    device = approved_device(rig.clock)
    old = await rig.dispatcher.register(device, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    fresh_keys = UserAgent().keys

    new = await rig.dispatcher.register(device, ChannelKind.WEB_PUSH, PUSH_URL, fresh_keys)

    assert new.id != old.id
    assert await rig.store.list_all() == (new,)
    assert new.keys == fresh_keys


async def test_register_refuses_a_device_past_its_subscription_limit() -> None:
    rig = _rig()
    device = approved_device(rig.clock)
    for number in range(MAX_SUBSCRIPTIONS_PER_DEVICE):
        await rig.dispatcher.register(device, ChannelKind.WEBHOOK, f"{HOOK_URL}&n={number}")

    with pytest.raises(PushRegistrationRefusedError, match="already holds"):
        await rig.dispatcher.register(device, ChannelKind.WEBHOOK, f"{HOOK_URL}&n=last")

    assert len(await rig.store.list_all()) == MAX_SUBSCRIPTIONS_PER_DEVICE


async def test_a_refused_registration_stores_nothing() -> None:
    rig = _rig()
    locked = make_device(rig.clock, DeviceStatus.LOCKED)

    with pytest.raises(PushRegistrationRefusedError):
        await rig.dispatcher.register(locked, ChannelKind.WEBHOOK, HOOK_URL)
    with pytest.raises(DestinationRefusedError):
        await rig.dispatcher.register(
            approved_device(rig.clock), ChannelKind.WEBHOOK, "https://169.254.169.254/latest"
        )

    assert await rig.store.list_all() == ()


# ──────────────────────────────────────────────────────────────────────────────
# push
# ──────────────────────────────────────────────────────────────────────────────


async def test_push_reaches_the_audience_on_every_channel_and_no_one_else() -> None:
    rig = _rig()
    phone, program = approved_device(rig.clock, *_ANSWERER), approved_device(rig.clock, *_ANSWERER)
    outsider = approved_device(rig.clock, "entrance:push")
    web = await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    hook = await rig.dispatcher.register(program, ChannelKind.WEBHOOK, HOOK_URL)
    await rig.dispatcher.register(outsider, ChannelKind.WEBHOOK, f"{HOOK_URL}&outsider=1")
    frames: list[str] = []
    rig.live.attach(phone.id, _recorder(frames))

    notice = await _question(rig, phone, program, outsider)

    assert rig.web_push.received(NoticeKind.QUESTION_WAITING) == [(_REF, web.id)]
    assert rig.webhook.received(NoticeKind.QUESTION_WAITING) == [(_REF, hook.id)]
    assert len(frames) == 1
    assert notice.event_id in frames[0]


async def test_push_records_who_received_it_and_reports_it() -> None:
    rig = _rig()
    phone = approved_device(rig.clock, *_ANSWERER)
    web = await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    rig.live.attach(phone.id, _recorder([]))
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, rig.clock)

    report = await rig.dispatcher.push(notice, audience_for(notice.kind, [phone]))

    assert report.delivered == frozenset({web.id})
    assert report.live_devices == frozenset({phone.id})
    assert await rig.store.recipients(_REF) == (web,)


async def test_push_deletes_a_subscription_its_push_service_says_is_gone() -> None:
    rig = _rig()
    phone = approved_device(rig.clock, *_ANSWERER)
    web = await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    rig.web_push.answer(web.id, DeliveryOutcome.GONE)

    await _question(rig, phone)

    assert await rig.store.list_all() == ()


async def test_one_channel_failing_never_stops_another_channels_delivery() -> None:
    rig = _rig(FakePush(default=DeliveryOutcome.RETRY_LATER))
    phone, program = approved_device(rig.clock, *_ANSWERER), approved_device(rig.clock, *_ANSWERER)
    web = await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    hook = await rig.dispatcher.register(program, ChannelKind.WEBHOOK, HOOK_URL)

    await _question(rig, phone, program)

    assert await rig.store.recipients(_REF) == (web,)
    assert set(await rig.store.list_all()) == {web, hook}


async def test_a_subscription_whose_channel_is_off_is_refused_and_kept() -> None:
    rig = _rig(web_push_on=False)
    phone = approved_device(rig.clock, *_ANSWERER)
    web = await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, rig.clock)

    report = await rig.dispatcher.push(notice, audience_for(notice.kind, [phone]))

    assert report.outcomes == {web.id: DeliveryOutcome.REFUSED}
    assert await rig.store.list_all() == (web,)


async def test_push_refuses_a_withdrawal_and_an_audience_built_for_another_kind() -> None:
    rig = _rig()
    withdrawn = PushNotice.mint(NoticeKind.WITHDRAWN, _REF, rig.clock)
    question = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, rig.clock)

    with pytest.raises(ValueError, match="use withdraw"):
        await rig.dispatcher.push(withdrawn, audience_for(NoticeKind.WITHDRAWN, []))
    with pytest.raises(ValueError, match="security_event"):
        await rig.dispatcher.push(question, audience_for(NoticeKind.SECURITY_EVENT, []))


# ──────────────────────────────────────────────────────────────────────────────
# withdraw
# ──────────────────────────────────────────────────────────────────────────────


async def test_withdraw_reaches_exactly_the_recipients_of_the_original() -> None:
    rig = _rig()
    phone, program, late = (approved_device(rig.clock, *_ANSWERER) for _ in range(3))
    web = await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    hook = await rig.dispatcher.register(program, ChannelKind.WEBHOOK, HOOK_URL)
    rig.webhook.answer(hook.id, DeliveryOutcome.RETRY_LATER)
    frames: list[str] = []
    rig.live.attach(phone.id, _recorder(frames))
    await _question(rig, phone, program)
    # A device that subscribes after the question went out never saw it.
    await rig.dispatcher.register(late, ChannelKind.WEBHOOK, f"{HOOK_URL}&late=1")

    report = await rig.dispatcher.withdraw(_REF)

    assert rig.web_push.received(NoticeKind.WITHDRAWN) == [(_REF, web.id)]
    assert rig.webhook.received(NoticeKind.WITHDRAWN) == []
    assert report.live_devices == frozenset({phone.id})
    assert '"kind":"withdrawn"' in frames[-1]


async def test_withdraw_is_sent_once_and_then_the_ref_is_forgotten() -> None:
    rig = _rig()
    phone = approved_device(rig.clock, *_ANSWERER)
    await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    await _question(rig, phone)

    await rig.dispatcher.withdraw(_REF)
    second = await rig.dispatcher.withdraw(_REF)

    assert len(rig.web_push.received(NoticeKind.WITHDRAWN)) == 1
    assert second.outcomes == {}
    assert await rig.store.recipients(_REF) == ()


async def test_withdraw_waits_for_a_push_of_the_same_ref_still_in_flight() -> None:
    gated = _GatedPush()
    clock = SteppingClock()
    store = MemorySubscriptionStore()
    channels = PushChannels(live=LivePush(), stored={ChannelKind.WEBHOOK: gated})
    dispatcher = PushDispatcher(
        channels, store, Admission(EntrancePushSection(), make_guard()), clock
    )
    program = approved_device(clock, *_ANSWERER)
    await dispatcher.register(program, ChannelKind.WEBHOOK, HOOK_URL)
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock)

    async with asyncio.TaskGroup() as group:
        group.create_task(dispatcher.push(notice, audience_for(notice.kind, [program])))
        await gated.started.wait()
        group.create_task(dispatcher.withdraw(_REF))
        # Let the withdrawal run as far as it can: it must stop at the ref's lock.
        for _ in range(10):
            await asyncio.sleep(0)
        assert gated.kinds == [NoticeKind.QUESTION_WAITING]
        gated.gate.set()

    assert gated.kinds == [NoticeKind.QUESTION_WAITING, NoticeKind.WITHDRAWN]


# ──────────────────────────────────────────────────────────────────────────────
# forget_device / revalidate
# ──────────────────────────────────────────────────────────────────────────────


async def test_forget_device_deletes_its_subscriptions_and_drops_its_sockets() -> None:
    rig = _rig()
    phone, other = approved_device(rig.clock), approved_device(rig.clock)
    web = await rig.dispatcher.register(phone, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)
    kept = await rig.dispatcher.register(other, ChannelKind.WEBHOOK, HOOK_URL)
    rig.live.attach(phone.id, _recorder([]))

    removed = await rig.dispatcher.forget_device(phone.id)

    assert removed == (web.id,)
    assert await rig.store.list_all() == (kept,)
    assert rig.live.live_devices() == frozenset()


async def test_revalidate_deletes_subscriptions_of_devices_no_longer_approved() -> None:
    rig = _rig()
    staying, leaving = approved_device(rig.clock), approved_device(rig.clock)
    kept = await rig.dispatcher.register(staying, ChannelKind.WEBHOOK, HOOK_URL)
    gone = await rig.dispatcher.register(leaving, ChannelKind.WEBHOOK, HOOK_URL)

    removed = await rig.dispatcher.revalidate([staying.id])

    assert removed == (gone.id,)
    assert await rig.store.list_all() == (kept,)


def _recorder(frames: list[str]) -> _FrameSender:
    """Return a live sender that appends every frame to ``frames``."""
    return _FrameSender(frames)


class _FrameSender:
    """A live sender that records frames."""

    def __init__(self, frames: list[str]) -> None:
        """Record into ``frames``."""
        self._frames = frames

    async def __call__(self, frame: str) -> None:
        """Record one frame."""
        self._frames.append(frame)


class _GatedPush:
    """A channel that holds every delivery until the test opens its gate."""

    def __init__(self) -> None:
        """Start closed, with nothing delivered."""
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.kinds: list[NoticeKind] = []

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Record the kind, then wait for the gate; see PushChannel.deliver."""
        self.kinds.append(notice.kind)
        self.started.set()
        await self.gate.wait()
        return DeliveryOutcome.DELIVERED
