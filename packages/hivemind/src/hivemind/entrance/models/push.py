"""Define the push resource's bodies: subscribing to push, and the keys a device verifies with.

A device holding ``entrance:push`` registers where it wants to hear that something is waiting
(ADR-0034): a webhook URL for a program, or a browser's Web Push subscription (its endpoint and
keys, exactly as ``PushSubscription.toJSON()`` gives them). The destination guard vets every
endpoint before it is stored. The two keys a device needs are public: the VAPID application
server key a browser subscribes with, and the Hive's Ed25519 key a webhook receiver verifies
``X-Hive-Signature`` with.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.push``; published in the OpenAPI document. Calls into the push
    channel's models and pydantic.

Key invariants:
    - A subscription is answered without its endpoint or keys: the device already has them.

See Also:
    - hivemind.entrance.push for the channels and the destination guard.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.push import ChannelKind, Subscription, WebPushKeys

MAX_ENDPOINT_FIELD_CHARS = 2_048  # A push service URL or a webhook URL; browsers keep them short.

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = [
    "HiveKeyView",
    "SubscribeBody",
    "SubscriptionView",
    "VapidKeyView",
    "subscription_view",
]


class SubscribeBody(BaseModel):
    """Register a push destination for the calling device (``entrance:push``)."""

    model_config = _CONFIG

    channel: ChannelKind = Field(description="webhook or web_push.")
    endpoint: str = Field(
        min_length=1,
        max_length=MAX_ENDPOINT_FIELD_CHARS,
        description="The webhook URL (https, inside [entrance] vpn_cidrs, or allowlisted), or "
        "the Web Push subscription's endpoint.",
    )
    keys: WebPushKeys | None = Field(
        default=None, description="For web_push: the subscription's p256dh and auth keys."
    )


class SubscriptionView(BaseModel):
    """A stored push subscription."""

    model_config = _CONFIG

    id: str = Field(description="The subscription's id (sub_...): delete it with this.")
    channel: ChannelKind = Field(description="webhook or web_push.")
    created_at: datetime = Field(description="When it was registered.")


class VapidKeyView(BaseModel):
    """The key a browser's PushManager.subscribe takes as applicationServerKey."""

    model_config = _CONFIG

    application_server_key: str = Field(
        description="The VAPID public key: an uncompressed P-256 point, unpadded base64url."
    )


class HiveKeyView(BaseModel):
    """The Hive's public signing key: what a webhook receiver verifies with."""

    model_config = _CONFIG

    public_key_hex: str = Field(description="The Hive's Ed25519 public key, 64 lowercase hex.")
    algorithm: str = Field(default="ed25519", description="Always ed25519.")


def subscription_view(subscription: Subscription) -> SubscriptionView:
    """Shape a stored subscription for the Landing Board, leaving its endpoint and keys behind.

    Args:
        subscription: The subscription as the push store holds it.

    Returns:
        Its view.
    """
    return SubscriptionView(
        id=subscription.id, channel=subscription.channel, created_at=subscription.created_at
    )
