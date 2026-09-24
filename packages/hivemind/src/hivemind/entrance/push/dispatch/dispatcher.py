"""Register push subscriptions, push each notice to its audience, and withdraw it everywhere.

``PushDispatcher`` is the push channel's one entry point (ADR-0034). ``register`` admits a device's
webhook URL or Web Push subscription through the registration gate and stores it (a repeat
registration of the same endpoint is the same subscription). ``push`` sends a notice to every live
socket and every stored subscription of the devices in its ``Audience`` (built by
``hivemind.entrance.push.audience.audience_for``, the pure rule), deletes destinations that are
gone, and records which subscriptions accepted it. ``withdraw`` sends a ``withdrawn`` notice with
the same ref to exactly the subscriptions and live devices that received the original, so a
question answered on one device disappears from every other. ``forget_device`` deletes a device's
subscriptions and drops its live sockets, the push half of offboarding a device (roadmap 10.5d);
``revalidate`` deletes every subscription whose device is no longer approved, run once at start
before the first delivery.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.dispatch``.
    Built by the Entrance's composition root; called by the subscribe route, by whatever raises a
    notice (inbox, Alarms, chat, enrolment) and by the device state changes. Calls into the
    registration gate, the ``Courier`` (channels and store) and the ``SubscriptionStore``.

Key invariants:
    - A push and a withdrawal of one ref never overlap: a withdrawal waits for the original's
      deliveries to finish and be recorded, so it reaches every copy that went out.
    - A notice goes only to an audience built for its own kind; a ``withdrawn`` notice goes only
      to the original's recipients.
    - No device holds more than ``MAX_SUBSCRIPTIONS_PER_DEVICE`` subscriptions.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the decisions.
    - hivemind.entrance.push.audience for who hears what.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence

from hivemind.common.logging import get_logger
from hivemind.entrance.enrol import EnrolledDevice
from hivemind.entrance.push.audience import Audience
from hivemind.entrance.push.dispatch.courier import Courier, PushChannels, PushReport
from hivemind.entrance.push.dispatch.refs import LiveRecipients, RefLocks
from hivemind.entrance.push.errors import PushRegistrationRefusedError, SubscriptionNotFoundError
from hivemind.entrance.push.models import (
    ChannelKind,
    NoticeKind,
    PushNotice,
    Subscription,
    SubscriptionId,
    WebPushKeys,
    new_subscription_id,
)
from hivemind.entrance.push.registration import Admission
from hivemind.entrance.push.store import SubscriptionStore
from waggle.clock import Clock
from waggle.ids import DeviceId

# A browser, an app and a hook or two; more would let one device multiply every notice into a
# flood of requests at someone else's URL.
MAX_SUBSCRIPTIONS_PER_DEVICE = 16
# Refs whose live recipients are remembered for a withdrawal; far more questions than are ever
# open at once, and a live device that misses a withdrawal re-fetches the inbox when it looks.
MAX_LIVE_REFS = 1024

log = get_logger(__name__)

__all__ = ["MAX_LIVE_REFS", "MAX_SUBSCRIPTIONS_PER_DEVICE", "PushDispatcher"]


class PushDispatcher:
    """The push channel's entry point: register, push, withdraw, forget, re-validate."""

    def __init__(
        self, channels: PushChannels, store: SubscriptionStore, admission: Admission, clock: Clock
    ) -> None:
        """Build the dispatcher.

        Args:
            channels: The live hub and the stored channels (webhook, Web Push).
            store: The push subscriptions and the delivery log.
            admission: The registration gate (the rules and the destination guard).
            clock: Mints subscription ids and notice ids, and stamps deliveries.
        """
        self._channels = channels
        self._store = store
        self._admission = admission
        self._clock = clock
        self._courier = Courier(channels, store, clock)
        self._ref_locks = RefLocks()
        self._live_recipients = LiveRecipients(MAX_LIVE_REFS)
        # Serialises register's read-decide-write, so one endpoint registered twice at once is
        # stored once.
        self._registering = asyncio.Lock()

    async def register(
        self,
        device: EnrolledDevice,
        channel: ChannelKind,
        endpoint: str,
        keys: WebPushKeys | None = None,
    ) -> Subscription:
        """Admit and store a device's push subscription.

        Args:
            device: The device registering, as the Entrance tables hold it now.
            channel: Webhook or Web Push.
            endpoint: The webhook URL or the push service endpoint.
            keys: The Web Push keys (``p256dh`` and ``auth``); None for a webhook.

        Returns:
            The stored subscription: the existing one when this device already registered the
            same endpoint with the same keys; a replacement when the keys changed.

        Raises:
            PushRegistrationRefusedError: The gate refuses it, or the device is at its limit.
            DestinationRefusedError: The destination guard refuses the endpoint.
        """
        # Latency: at most one DNS lookup in the guard; nothing is stored unless it passes.
        await self._admission.admit(device, channel, endpoint, keys)
        async with self._registering:
            subscription = await self._store_or_reuse(device.id, channel, endpoint, keys)
        log.info(
            "push.subscribed",
            subscription_id=subscription.id,
            device_id=device.id,
            channel=channel.value,
        )
        return subscription

    async def push(self, notice: PushNotice, audience: Audience) -> PushReport:
        """Send ``notice`` to every live socket and stored subscription of its audience.

        Args:
            notice: The notice; never a ``withdrawn`` one (see ``withdraw``).
            audience: ``audience_for(notice.kind, ...)``.

        Returns:
            What happened to each destination.

        Raises:
            ValueError: ``notice`` is a withdrawal, or ``audience`` was built for another kind.
        """
        if notice.kind is NoticeKind.WITHDRAWN:
            raise ValueError("A withdrawn notice goes to the original's recipients; use withdraw.")
        if audience.kind is not notice.kind:
            raise ValueError(f"This audience was built for {audience.kind.value} notices.")
        async with self._ref_locks.hold(notice.ref):
            subscriptions = [
                subscription
                for subscription in await self._store.list_all()
                if subscription.device_id in audience.device_ids
            ]
            report = await self._send(notice, subscriptions, audience.device_ids, record=True)
            self._live_recipients.add(notice.ref, report.live_devices)
        return report

    async def withdraw(self, ref: str) -> PushReport:
        """Send a ``withdrawn`` notice for ``ref`` to exactly the recipients of the original.

        Args:
            ref: The ref of the question answered, the Alarm resolved, the item withdrawn.

        Returns:
            What happened to each recipient; empty when nothing had received the original.
        """
        notice = PushNotice.mint(NoticeKind.WITHDRAWN, ref, self._clock)
        # Waits for a push of this ref still in flight, so its recipients are recorded first.
        async with self._ref_locks.hold(ref):
            recipients = await self._store.recipients(ref)
            live = self._live_recipients.take(ref)
            report = await self._send(notice, recipients, live, record=False)
            # Withdrawn is final: the log's rows for this ref have done their job.
            await self._store.forget_ref(ref)
        log.info(
            "push.withdrawn",
            event_id=notice.event_id,
            subscriptions=len(recipients),
            live_devices=len(report.live_devices),
        )
        return report

    async def unregister(self, device_id: DeviceId, subscription_id: SubscriptionId) -> None:
        """Delete one of a device's own subscriptions (the device asked to stop hearing there).

        Args:
            device_id: The device asking.
            subscription_id: Its subscription.

        Raises:
            SubscriptionNotFoundError: The device holds no such subscription (another device's
                reads as missing, so ids learn nothing).
        """
        # Latency: one local read and, when it is the device's, one local delete.
        held = await self._store.list_for_device(device_id)
        if all(subscription.id != subscription_id for subscription in held):
            raise SubscriptionNotFoundError(subscription_id, device_id)
        await self._store.delete(subscription_id)
        log.info("push.unsubscribed", subscription_id=subscription_id, device_id=device_id)

    async def forget_device(self, device_id: DeviceId) -> tuple[SubscriptionId, ...]:
        """Delete every subscription of a device and drop its live sockets (offboarding).

        Args:
            device_id: The device that left APPROVED (revoked, locked, expired) or is removed.

        Returns:
            The subscription ids deleted.
        """
        removed = await self._store.delete_for_device(device_id)
        dropped = self._channels.live.detach_device(device_id)
        log.info(
            "push.device_forgotten", device_id=device_id, subscriptions=len(removed), live=dropped
        )
        return removed

    async def revalidate(
        self, approved_device_ids: Collection[DeviceId]
    ) -> tuple[SubscriptionId, ...]:
        """Delete every subscription whose device is not approved; run at start.

        Args:
            approved_device_ids: Every device the Entrance tables hold as APPROVED right now.

        Returns:
            The subscription ids deleted.
        """
        approved = frozenset(approved_device_ids)
        removed: list[SubscriptionId] = []
        # A device that left APPROVED while the Hive was down still holds its rows: drop them.
        for subscription in await self._store.list_all():
            if subscription.device_id not in approved:
                await self._store.delete(subscription.id)
                removed.append(subscription.id)
        log.info("push.revalidated", removed=len(removed))
        return tuple(removed)

    async def _store_or_reuse(
        self, device_id: DeviceId, channel: ChannelKind, endpoint: str, keys: WebPushKeys | None
    ) -> Subscription:
        """Return the device's identical subscription, or store a new one over a stale one."""
        existing = await self._store.list_for_device(device_id)
        same = next((s for s in existing if s.channel is channel and s.endpoint == endpoint), None)
        # A page that subscribes on every load registers the same thing again: same answer.
        if same is not None and same.keys == keys:
            return same
        if same is None and len(existing) >= MAX_SUBSCRIPTIONS_PER_DEVICE:
            limit = f"it already holds {MAX_SUBSCRIPTIONS_PER_DEVICE} push subscriptions"
            raise PushRegistrationRefusedError(device_id, limit)
        subscription = Subscription(
            id=new_subscription_id(self._clock),
            device_id=device_id,
            channel=channel,
            endpoint=endpoint,
            keys=keys,
            created_at=self._clock.now(),
        )
        # The same endpoint with new keys (a browser that re-subscribed) replaces the old one.
        if same is not None:
            await self._store.delete(same.id)
        await self._store.add(subscription)
        return subscription

    async def _send(
        self,
        notice: PushNotice,
        subscriptions: Sequence[Subscription],
        live_devices: frozenset[DeviceId],
        *,
        record: bool,
    ) -> PushReport:
        """Deliver through the courier, then settle: delete what is gone, record what arrived."""
        report = await self._courier.deliver(notice, subscriptions, live_devices)
        await self._courier.settle(report, subscriptions, record=record)
        return report
