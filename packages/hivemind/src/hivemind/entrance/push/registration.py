"""Decide whether a device may register a push subscription, and vet where it points.

A subscription is a standing instruction for the Hive to send requests somewhere on a device's
behalf, so registering one is gated (ADR-0034): the device must be APPROVED and hold
``entrance:push``; ``[entrance.push]`` must offer the channel it asks for; the keys must fit the
channel (a Web Push subscription brings its ``p256dh`` and ``auth`` keys and an https endpoint, a
webhook brings none); and the destination must pass the destination guard. The guard is applied
to Web Push endpoints as well as webhooks: a push service endpoint is also a URL a device chose,
and a real one (always https, on the public internet) passes it untouched. ``registration_refusal``
is the pure part; ``Admission`` adds the guard's lookup.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Called by
    ``hivemind.entrance.push.dispatch.PushDispatcher.register`` before anything is stored. Calls
    into the destination guard, ``hivemind.guard`` (the capability grammar),
    ``hivemind.entrance.enrol`` (the device record) and the ``[entrance.push]`` section.

Key invariants:
    - Nothing is stored and nothing is sent by this module; it only refuses or allows.
    - A refusal says why without echoing the URL or the keys.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for who may subscribe.
    - hivemind.entrance.push.destinations for the destination rules.
"""

from __future__ import annotations

from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice
from hivemind.entrance.push.audience import PUSH_CAPABILITY, held_capabilities
from hivemind.entrance.push.destinations import DestinationGuard
from hivemind.entrance.push.errors import PushRegistrationRefusedError
from hivemind.entrance.push.models import ChannelKind, WebPushKeys
from hivemind.manifest.schema import EntrancePushSection

__all__ = ["Admission", "registration_refusal"]


class Admission:
    """The registration gate: the pure rules, then the destination guard."""

    def __init__(self, settings: EntrancePushSection, guard: DestinationGuard) -> None:
        """Build the gate.

        Args:
            settings: ``[entrance.push]``: which channels are offered.
            guard: Vets the endpoint the device registers.
        """
        self._settings = settings
        self._guard = guard

    async def admit(
        self,
        device: EnrolledDevice,
        channel: ChannelKind,
        endpoint: str,
        keys: WebPushKeys | None,
    ) -> None:
        """Refuse the registration unless every rule and the destination guard allow it.

        Args:
            device: The device asking, as the Entrance tables hold it now.
            channel: The channel it asks for.
            endpoint: The webhook URL or push service endpoint.
            keys: The Web Push keys, or None for a webhook.

        Raises:
            PushRegistrationRefusedError: A rule refuses it.
            DestinationRefusedError: The destination guard refuses the endpoint.
        """
        refusal = registration_refusal(device, channel, keys, self._settings)
        if refusal is not None:
            raise PushRegistrationRefusedError(device.id, refusal)
        # Latency: at most one DNS lookup, bounded by the guard's own timeout.
        vetted = await self._guard.vet(endpoint)
        # RFC 8030 push services are https only; plain http here is a misconfigured device.
        if channel is ChannelKind.WEB_PUSH and vetted.destination.scheme != "https":
            raise PushRegistrationRefusedError(device.id, "a Web Push endpoint is always https")


def registration_refusal(
    device: EnrolledDevice,
    channel: ChannelKind,
    keys: WebPushKeys | None,
    settings: EntrancePushSection,
) -> str | None:
    """Return why ``device`` may not register on ``channel``, or None when it may.

    Args:
        device: The device asking.
        channel: The channel it asks for.
        keys: The Web Push keys it sent, or None.
        settings: ``[entrance.push]``.

    Returns:
        A sentence fragment naming the rule that refuses it, or None.
    """
    # Only an approved device may subscribe; a locked or pending one hears nothing.
    if device.status is not DeviceStatus.APPROVED:
        return f"it is {device.status.name}, not APPROVED"
    # ADR-0034: entrance:push is what lets a device hear anything at all.
    if not held_capabilities(device).allows(PUSH_CAPABILITY):
        return "it does not hold entrance:push"
    # The operator may turn either channel off; a channel that is off takes no new subscriptions.
    offered = {ChannelKind.WEBHOOK: settings.webhooks, ChannelKind.WEB_PUSH: settings.web_push}
    if not offered[channel]:
        return f"[entrance.push] does not offer the {channel.value} channel"
    # Without keys nothing could be encrypted to the user agent; a webhook has no use for them.
    if (keys is not None) != (channel is ChannelKind.WEB_PUSH):
        return "a web_push subscription needs its p256dh and auth keys, and a webhook takes none"
    return None
