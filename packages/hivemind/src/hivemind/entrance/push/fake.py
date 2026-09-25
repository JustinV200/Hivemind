"""Provide FakePush: a PushChannel that records every delivery and answers as scripted.

Codingrules 14.4 keeps fakes beside their protocol in ``src/``, honest and production quality,
because demos and ``hive doctor`` use them as well as tests. ``FakePush`` stands in for a webhook or
Web Push channel: it keeps every (notice, subscription) pair it was asked to deliver, in order, and
answers DELIVERED unless a test scripted another outcome for a subscription, so a dispatcher test
can make one destination gone, refused or failing and watch the rest go on. It sends nothing.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Injected in
    place of ``WebhookPush`` or ``WebPush`` wherever a ``PushDispatcher`` is built for a test or
    a demo. Imports the push records only.

Key invariants:
    - Every call to ``deliver`` is recorded before it answers, whatever the answer.
    - It never raises from ``deliver``, like every PushChannel.

See Also:
    - hivemind.entrance.push.base for the PushChannel protocol.
"""

from __future__ import annotations

from hivemind.entrance.push.models import (
    DeliveryOutcome,
    NoticeKind,
    PushNotice,
    Subscription,
    SubscriptionId,
)

__all__ = ["FakePush"]


class FakePush:
    """A recording PushChannel whose answer per subscription a test can script."""

    def __init__(self, default: DeliveryOutcome = DeliveryOutcome.DELIVERED) -> None:
        """Build the fake.

        Args:
            default: What every subscription without a scripted outcome answers.
        """
        self.deliveries: list[tuple[PushNotice, Subscription]] = []
        self._default = default
        self._scripted: dict[SubscriptionId, DeliveryOutcome] = {}

    def answer(self, subscription_id: SubscriptionId, outcome: DeliveryOutcome) -> None:
        """Script what deliveries to one subscription answer from now on.

        Args:
            subscription_id: The subscription.
            outcome: What ``deliver`` returns for it.
        """
        self._scripted[subscription_id] = outcome

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Record the delivery and return the scripted outcome; see PushChannel.deliver."""
        self.deliveries.append((notice, subscription))
        return self._scripted.get(subscription.id, self._default)

    def received(self, kind: NoticeKind) -> list[tuple[str, SubscriptionId]]:
        """Return every (ref, subscription id) that was sent a notice of ``kind``, in order.

        Args:
            kind: The notice kind to filter on.

        Returns:
            One pair per recorded delivery of that kind.
        """
        return [
            (notice.ref, subscription.id)
            for notice, subscription in self.deliveries
            if notice.kind is kind
        ]
