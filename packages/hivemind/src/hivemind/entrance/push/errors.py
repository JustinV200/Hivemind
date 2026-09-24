"""Define PushError and every way the Entrance's push channel refuses something on purpose.

The push channel (ADR-0034) tells an enrolled device that something is waiting for the human, over
a live WebSocket, a signed webhook or Web Push. It refuses a few things by design: a registration
from a device that may not subscribe, a destination that points inside the Hive, a subscription
recorded twice, a malformed push key. Every such refusal is one class here, rooted at
``PushError``, itself an ``hivemind.entrance.errors.EntranceError`` so a caller that catches the
whole Entrance family catches these too, and each also subclasses the ``hivemind.common.errors``
category it belongs to so an HTTP status mapper can react by category. Every class carries its
own stable, dotted ``code`` (codingrules section 10).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Raised by the
    destination guard, the registration gate, the subscription stores and the push key loaders;
    raised by a live sender to say its socket is gone. Imports only the error roots.

Key invariants:
    - Every class sets its own ``code``, prefixed ``hivemind.entrance.``, and no two share one.
    - No message ever carries a URL, a key, an auth secret or a payload: messages name devices,
      subscriptions, secrets and environment variables by id or name only (codingrules 12, 13).
    - A destination refusal's reason is typed as ``Enum`` here, not ``DestinationRefusal``, so
      this module never imports ``hivemind.entrance.push.destinations``, which imports it.

See Also:
    - hivemind.entrance.errors for the Entrance's root and its other refusals.
    - docs/adr/0034-landing-board-versioning-and-push.md for what the push channel refuses.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import ConflictError, PermissionDeniedError
from hivemind.entrance.errors import EntranceError

__all__ = [
    "DestinationRefusedError",
    "LiveSocketClosedError",
    "PushConfigError",
    "PushError",
    "PushRegistrationRefusedError",
    "SubscriptionExistsError",
]


class PushError(EntranceError):
    """Root of every error the Entrance's push channel raises on purpose."""

    code: ClassVar[str] = "hivemind.entrance.push_error"


class PushRegistrationRefusedError(PushError, PermissionDeniedError):
    """Raise when a device may not register the push subscription it asked for.

    The device is not APPROVED, lacks ``entrance:push``, asked for a channel ``[entrance.push]``
    turns off, sent keys that do not fit the channel, or already holds the most subscriptions a
    device may.
    """

    code: ClassVar[str] = "hivemind.entrance.push_registration_refused"

    def __init__(self, device_id: str, reason: str) -> None:
        """Build the error for one refused registration.

        Args:
            device_id: The device that asked.
            reason: Why, as a sentence fragment naming no URL and no key.
        """
        super().__init__(f"Device {device_id} may not register this push subscription: {reason}.")
        self.device_id = device_id
        self.reason = reason


class DestinationRefusedError(PushError, PermissionDeniedError):
    """Raise when a push destination could reach inside the Hive, or is not safe to send to.

    Carries the refusal's reason (a ``DestinationRefusal``), never the URL: a Web Push endpoint is
    a capability URL, and a webhook URL may carry a token in its query.
    """

    code: ClassVar[str] = "hivemind.entrance.push_destination_refused"

    def __init__(self, reason: Enum) -> None:
        """Build the error for one refused destination.

        Args:
            reason: The ``hivemind.entrance.push.destinations.DestinationRefusal`` that applied.
        """
        super().__init__(
            f"The push destination was refused ({reason.value}); a push never goes to a "
            "loopback, link-local, unspecified or multicast address or to the Hive Stand itself, "
            "and plain http only inside the VPN or the webhook allowlist."
        )
        self.reason = reason


class SubscriptionExistsError(PushError, ConflictError):
    """Raise when a subscription id, or a device's channel and endpoint pair, is already stored."""

    code: ClassVar[str] = "hivemind.entrance.push_subscription_exists"

    def __init__(self, subscription_id: str, device_id: str) -> None:
        """Build the error for a duplicate subscription.

        Args:
            subscription_id: The id the caller tried to add.
            device_id: The device it belongs to.
        """
        super().__init__(
            f"Push subscription {subscription_id} for device {device_id} duplicates a stored "
            "one (the same id, or the same channel and endpoint for that device)."
        )
        self.subscription_id = subscription_id
        self.device_id = device_id


class PushConfigError(PushError):
    """Raise when a push key or setting (the VAPID key or subject, the Topic key) is malformed.

    The message names where the value came from, an environment variable or a secret, never the
    value or its length.
    """

    code: ClassVar[str] = "hivemind.entrance.push_config_invalid"

    def __init__(self, source: str, rule: str) -> None:
        """Build the error for one malformed value.

        Args:
            source: The environment variable or secret name the value was read from.
            rule: What a valid value looks like, e.g. "base64url of a raw 32-byte P-256 scalar".
        """
        super().__init__(f"The push setting in {source} is not valid: it must be {rule}.")
        self.source = source


class LiveSocketClosedError(PushError):
    """Raise from a live sender whose WebSocket is gone; the live hub then detaches it.

    The route that attaches a sender adapts its framework's own disconnect errors into this one,
    so the hub catches one typed error instead of everything (codingrules section 10).
    """

    code: ClassVar[str] = "hivemind.entrance.push_socket_closed"
