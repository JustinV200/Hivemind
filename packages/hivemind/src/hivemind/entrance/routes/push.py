"""Serve the push resource: register and delete push destinations, and the keys to verify with.

A device holding ``entrance:push`` tells the Entrance where it wants to hear that something is
waiting (ADR-0034): a webhook URL (a program) or a Web Push subscription (a browser or phone).
Registration goes through the push dispatcher's gate (the device's standing, the channel being
offered, and the destination guard, which refuses any endpoint that resolves inside the Hive). A
device deletes only its own subscriptions. The VAPID public key is what a browser subscribes with;
the Hive's Ed25519 public key is what a webhook receiver verifies ``X-Hive-Signature`` with. The
live channel, ``/v1/push/stream``, is a stream view (``hivemind.entrance.streams``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    the route table. Calls into ``hivemind.entrance.push`` (the dispatcher).

Key invariants:
    - A subscription's endpoint and keys are never answered back or logged.
    - A device can delete no one's subscription but its own.

See Also:
    - hivemind.entrance.push for the channels, the gate and the destination guard.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.models import (
    HiveKeyView,
    SubscribeBody,
    SubscriptionView,
    VapidKeyView,
    subscription_view,
)
from hivemind.entrance.push import PushRegistrationRefusedError, SubscriptionId

PUSH = "entrance:push"  # What every push route needs.

SubscriptionIdPath = Annotated[
    str, Path(pattern=r"^sub_[0-9A-HJKMNP-TV-Z]{26}$", description="The subscription's id.")
]

__all__ = ["ROUTES"]


async def subscribe(
    body: SubscribeBody, caller: CallerParam, services: Services
) -> SubscriptionView:
    """Register a push destination for the calling device.

    Args:
        body: The channel, the endpoint and, for Web Push, the keys.
        caller: The admitted caller.
        services: The Entrance's services (the dispatcher).

    Returns:
        The stored subscription (the same one when this endpoint was already registered).
    """
    # Latency: at most one DNS lookup in the destination guard, then one local write.
    subscription = await services.push.dispatcher.register(
        caller.device, body.channel, body.endpoint, body.keys
    )
    return subscription_view(subscription)


async def unsubscribe(
    subscription_id: SubscriptionIdPath, caller: CallerParam, services: Services
) -> None:
    """Delete one of the calling device's push subscriptions.

    Args:
        subscription_id: The subscription.
        caller: The admitted caller.
        services: The Entrance's services (the dispatcher).
    """
    await services.push.dispatcher.unregister(caller.device.id, SubscriptionId(subscription_id))


async def vapid_key(caller: CallerParam, services: Services) -> VapidKeyView:
    """Return the VAPID public key a browser subscribes with.

    Args:
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The application server key.

    Raises:
        PushRegistrationRefusedError: Web Push is turned off in ``[entrance.push]``.
    """
    key = services.push.vapid_public_key
    if key is None:
        raise PushRegistrationRefusedError(
            caller.device.id, "[entrance.push] does not offer web_push"
        )
    return VapidKeyView(application_server_key=key)


async def hive_key(services: Services) -> HiveKeyView:
    """Return the Hive's Ed25519 public key, which signs every webhook.

    Args:
        services: The Entrance's services.

    Returns:
        The key in hex.
    """
    return HiveKeyView(public_key_hex=services.push.hive_public_key.hex())


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path="/v1/push/subscriptions",
        listeners=BOTH_LISTENERS,
        access=session_with(PUSH),
        effect=RouteEffect.PUSH,
        endpoint=subscribe,
        summary="Register a webhook or Web Push destination for this device.",
        status_code=201,
        response_model=SubscriptionView,
    ),
    RouteSpec(
        method="DELETE",
        path="/v1/push/subscriptions/{subscription_id}",
        listeners=BOTH_LISTENERS,
        access=session_with(PUSH),
        effect=RouteEffect.PUSH,
        endpoint=unsubscribe,
        summary="Delete one of this device's push subscriptions.",
        status_code=204,
    ),
    RouteSpec(
        method="GET",
        path="/v1/push/vapid-key",
        listeners=BOTH_LISTENERS,
        access=session_with(PUSH),
        effect=RouteEffect.READ,
        endpoint=vapid_key,
        summary="Read the VAPID public key a browser subscribes with.",
        response_model=VapidKeyView,
    ),
    RouteSpec(
        method="GET",
        path="/v1/push/hive-key",
        listeners=BOTH_LISTENERS,
        access=session_with(PUSH),
        effect=RouteEffect.READ,
        endpoint=hive_key,
        summary="Read the Hive's public key, which signs every webhook.",
        response_model=HiveKeyView,
    ),
)
