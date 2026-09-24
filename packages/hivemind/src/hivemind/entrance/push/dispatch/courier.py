"""Carry one notice to many destinations at once, and settle what their outcomes require.

``Courier`` is the delivery half of the push dispatcher: given a notice, the stored subscriptions
it goes to and the devices to try over a live socket, it delivers to every one of them
concurrently, each on its own task, so one webhook's backoff never holds up a phone's push and no
channel's failure cancels another's delivery; then it settles the outcomes: a destination that is
gone for good is deleted with its delivery log rows, and the subscriptions that accepted an
original notice are recorded against its ref for a later withdrawal. ``PushChannels`` is what it
delivers through; ``PushReport`` is what it says happened.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.dispatch``. Used
    by ``PushDispatcher.push`` and ``PushDispatcher.withdraw``. Calls into every ``PushChannel``,
    the live hub and the ``SubscriptionStore``.

Key invariants:
    - Every destination gets its own task in one ``TaskGroup``; ``deliver`` never raises for a
      failed delivery, and a channel's typed ``PushError`` becomes REFUSED here, so no sibling is
      ever cancelled by another's failure.
    - A subscription whose channel is not configured (``[entrance.push]`` turned it off after it
      was registered) is REFUSED and kept; nothing is sent.
    - Only DELIVERED subscriptions of an original notice are recorded; a withdrawal never is.

See Also:
    - hivemind.entrance.push.dispatch.dispatcher for the caller.
    - docs/adr/0034-landing-board-versioning-and-push.md for delivery and withdrawal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from hivemind.common.logging import get_logger
from hivemind.entrance.push.base import PushChannel
from hivemind.entrance.push.errors import PushError
from hivemind.entrance.push.models import (
    ChannelKind,
    DeliveryOutcome,
    PushNotice,
    Subscription,
    SubscriptionId,
)
from hivemind.entrance.push.store import SubscriptionStore
from hivemind.entrance.push.websocket import LivePush
from waggle.clock import Clock
from waggle.ids import DeviceId

log = get_logger(__name__)

__all__ = ["Courier", "PushChannels", "PushReport"]


@dataclass(frozen=True, slots=True)
class PushChannels:
    """The transports a dispatcher delivers through.

    Attributes:
        live: The live WebSocket hub, keyed by device.
        stored: One channel per stored subscription kind; a kind ``[entrance.push]`` turns off
            is simply absent.
    """

    live: LivePush
    stored: Mapping[ChannelKind, PushChannel]


@dataclass(frozen=True, slots=True)
class PushReport:
    """What one push or withdrawal did.

    Attributes:
        notice: The notice sent.
        outcomes: Each stored subscription tried, and how its delivery ended.
        live_devices: The devices at least one live socket of which took the notice.
    """

    notice: PushNotice
    outcomes: Mapping[SubscriptionId, DeliveryOutcome]
    live_devices: frozenset[DeviceId]

    @property
    def delivered(self) -> frozenset[SubscriptionId]:
        """The stored subscriptions that accepted the notice."""
        return frozenset(
            subscription_id
            for subscription_id, outcome in self.outcomes.items()
            if outcome is DeliveryOutcome.DELIVERED
        )


class Courier:
    """Deliver a notice to many destinations concurrently, then settle the outcomes."""

    def __init__(self, channels: PushChannels, store: SubscriptionStore, clock: Clock) -> None:
        """Build the courier.

        Args:
            channels: The live hub and the stored channels.
            store: Where gone subscriptions are deleted and deliveries recorded.
            clock: Stamps each recorded delivery.
        """
        self._channels = channels
        self._store = store
        self._clock = clock

    async def deliver(
        self,
        notice: PushNotice,
        subscriptions: Sequence[Subscription],
        live_devices: frozenset[DeviceId],
    ) -> PushReport:
        """Send ``notice`` to every subscription and every live device, all at once.

        Args:
            notice: The notice.
            subscriptions: The stored destinations.
            live_devices: The devices to try over their live sockets.

        Returns:
            What happened to each destination.
        """
        # Latency: the slowest destination's, each bounded by its channel's timeouts.
        async with asyncio.TaskGroup() as group:
            stored = {s.id: group.create_task(self._deliver_one(notice, s)) for s in subscriptions}
            live = {
                device: group.create_task(self._channels.live.deliver(notice, device))
                for device in live_devices
            }
        reached = {
            device for device, task in live.items() if task.result() is DeliveryOutcome.DELIVERED
        }
        outcomes = {subscription_id: task.result() for subscription_id, task in stored.items()}
        return PushReport(
            notice=notice,
            outcomes=MappingProxyType(outcomes),
            live_devices=frozenset(reached),
        )

    async def settle(
        self, report: PushReport, subscriptions: Sequence[Subscription], *, record: bool
    ) -> None:
        """Delete the destinations that are gone, and record who accepted an original notice.

        Args:
            report: What ``deliver`` returned.
            subscriptions: The subscriptions it was given.
            record: True for an original notice; False for a withdrawal, which is never itself
                withdrawn.
        """
        # A 404 or 410 from a push service means the user agent unsubscribed: forget it.
        for subscription in subscriptions:
            if report.outcomes.get(subscription.id) is DeliveryOutcome.GONE:
                await self._store.delete(subscription.id)
                log.info(
                    "push.subscription_gone",
                    subscription_id=subscription.id,
                    device_id=subscription.device_id,
                )
        if record and report.delivered:
            # Latency: one local write; the withdrawal reads it back, possibly after a restart.
            await self._store.record_delivery(
                report.notice.ref, report.delivered, self._clock.now()
            )

    async def _deliver_one(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Deliver to one stored subscription through its channel, as an outcome in every case."""
        channel = self._channels.stored.get(subscription.channel)
        # [entrance.push] turned this channel off after the subscription was made: keep the
        # subscription (the switch may come back on) and send nothing.
        if channel is None:
            outcome = DeliveryOutcome.REFUSED
        else:
            try:
                # Latency: the channel's own, bounded by its timeouts (and a webhook's backoff).
                outcome = await channel.deliver(notice, subscription)
            except PushError as error:
                # A channel's typed refusal is an outcome here, never a cancelled sibling task.
                log.warning("push.channel_error", subscription_id=subscription.id, code=error.code)
                outcome = DeliveryOutcome.REFUSED
        # A refusal means a registered destination now points somewhere forbidden: worth a look.
        if outcome is DeliveryOutcome.REFUSED:
            log.warning("push.refused", subscription_id=subscription.id, event_id=notice.event_id)
        elif outcome is not DeliveryOutcome.DELIVERED:
            log.info(
                "push.not_delivered",
                subscription_id=subscription.id,
                event_id=notice.event_id,
                outcome=outcome.value,
            )
        return outcome
