"""Define PushChannel, the protocol every stored push transport implements.

A push channel carries one ``PushNotice`` to one stored ``Subscription`` and says how that went
(ADR-0034): ``hivemind.entrance.push.webhook.WebhookPush`` posts it, signed with the Hive's key, to
a program's URL; ``hivemind.entrance.push.web_push.WebPush`` encrypts it for a browser or a phone
and hands it to their push service; ``hivemind.entrance.push.fake.FakePush`` records it for tests.
Withdrawal is not a second method: a ``withdrawn`` notice with the original's ``ref`` travels
through ``deliver`` like any other, so one notice shape serves every transport and a new channel
has exactly one thing to implement. The live WebSocket hub
(``hivemind.entrance.push.websocket.LivePush``) is keyed by device rather than by a stored
subscription, so the dispatcher calls it directly.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Called by
    ``hivemind.entrance.push.dispatch.PushDispatcher`` once per subscription per notice.
    Implemented beside it; imports only the push records.

Key invariants:
    - ``deliver`` never raises for a failed delivery: a refused destination, a network error or
      an HTTP status is a ``DeliveryOutcome``, so one channel's failure can never cancel another
      channel's delivery running beside it.
    - ``deliver`` checks the destination again before sending, every time (ADR-0034), and sends
      nothing when the check refuses it.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the channels and the notice.
    - .claude/codingrules.md section 8.1 for the protocol table this seam appears in.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.entrance.push.models import DeliveryOutcome, PushNotice, Subscription

__all__ = ["PushChannel"]


class PushChannel(Protocol):
    """Deliver one notice to one stored subscription over one transport.

    Implementations hold their own client and keys, are safe to call concurrently for different
    subscriptions from one event loop, and bound every network wait with a timeout.
    """

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Send ``notice`` to ``subscription`` and report how it ended.

        Args:
            notice: The notice; its event id stays the same across every retry.
            subscription: Where to send it; its channel is this channel's kind.

        Returns:
            DELIVERED when accepted; RETRY_LATER for a transient failure; GONE when the
            destination no longer exists (the caller deletes the subscription); REFUSED when the
            destination guard refused it (nothing was sent); REJECTED for a final refusal by the
            receiver.
        """
        ...
