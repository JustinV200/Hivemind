"""Define the push channel's records: the notice, the subscription, and how a delivery ended.

A push tells an enrolled device that something is waiting for the human, never what (ADR-0034):
``PushNotice`` has an event id, a kind, the id of the thing it points at (``ref``) and a time, and
no content field at all, so there is nothing a push service or a webhook receiver could read beyond
"the Hive wants this device's attention". ``notice_json`` is the one byte form every channel sends
(sorted keys, compact), so a webhook's signed body, a Web Push plaintext and a WebSocket frame are
the same bytes. ``Subscription`` is one device's registered destination, a webhook URL or a Web
Push endpoint with its ``p256dh`` and ``auth`` keys (``WebPushKeys``, the shape a browser's
``PushSubscription.toJSON()`` returns); the live WebSocket is not persisted, so it has no
``ChannelKind``. ``DeliveryOutcome`` is how one delivery ended, which the dispatcher acts on.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Built by the
    dispatcher (notices, subscriptions) and the subscribe route; stored by
    ``hivemind.entrance.push.store``; sent by every ``PushChannel``. Calls into
    ``hivemind.entrance.auth`` (base64url), ``cryptography`` (the P-256 point check) and waggle
    (ids, the clock, the ULID encoding).

Key invariants:
    - A notice holds no content: event id, kind, ref and time are its only fields, and ``ref`` is
      an identifier (letters, digits, ``_.:-``), never free text.
    - ``notice_json`` of the longest possible notice fits Web Push's 512-byte padded plaintext
      with room to spare (``MAX_REF_CHARS`` bounds the only variable-length field).
    - A web_push subscription carries keys that decode to a P-256 point on the curve and a
      16-byte auth secret, and an https endpoint; a webhook subscription carries no keys.
    - ``repr`` of a subscription never shows its endpoint or its keys (a Web Push endpoint is a
      capability URL; ``auth`` is a shared secret).

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the notice and the channels.
    - hivemind.entrance.push.base for the PushChannel protocol these records cross.
"""

from __future__ import annotations

import json
import secrets
from enum import Enum
from typing import Annotated, NewType

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from hivemind.entrance.auth import b64url_decode
from waggle.clock import Clock
from waggle.ids import new_event_id
from waggle.messages.base import DeviceIdField, EventIdField, UtcDatetime
from waggle.ulid import encode_ulid

MAX_REF_CHARS = 128  # A prefixed ULID is 32 characters; four times that leaves room, not prose.
MAX_ENDPOINT_CHARS = 2048  # Push service endpoints run to a few hundred characters.
P256_POINT_BYTES = 65  # An uncompressed SEC1 point, 0x04 || X || Y: what p256dh decodes to.
AUTH_SECRET_BYTES = 16  # RFC 8291 section 3.2: the user agent's auth secret is 16 octets.
SUBSCRIPTION_ID_PREFIX = "sub_"  # Distinguishes a push subscription id in a log line.
_ULID_RANDOM_BYTES = 10  # A ULID's 80 random bits, drawn fresh for every subscription id.
_MILLISECONDS_PER_SECOND = 1000  # A ULID's timestamp is in milliseconds.
_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"  # An id such as msg_01J..., never free text.
_SUBSCRIPTION_ID_PATTERN = r"^sub_[0-9A-HJKMNP-TV-Z]{26}$"  # sub_ plus a Crockford ULID.
_ENDPOINT_SCHEMES = ("https://", "http://")  # The guard decides which may be used where.
# JSON the one way every channel sends it: sorted keys, no whitespace, so a signature over the
# body and a receiver's re-serialisation never disagree on spacing or order.
_JSON_SEPARATORS = (",", ":")

# A frozen, extras-forbidding config every model here shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "AUTH_SECRET_BYTES",
    "MAX_ENDPOINT_CHARS",
    "MAX_REF_CHARS",
    "P256_POINT_BYTES",
    "SUBSCRIPTION_ID_PREFIX",
    "ChannelKind",
    "DeliveryOutcome",
    "NoticeKind",
    "PushNotice",
    "Subscription",
    "SubscriptionId",
    "WebPushKeys",
    "new_subscription_id",
    "notice_json",
]

SubscriptionId = NewType("SubscriptionId", str)  # A push subscription's id: sub_ plus a ULID.


class NoticeKind(Enum):
    """What a notice says is waiting; the device fetches the item itself when the human looks."""

    QUESTION_WAITING = "question_waiting"  # A question for the human is in the inbox.
    ALARM_WAITING = "alarm_waiting"  # An Alarm reached the human.
    REPLY_WAITING = "reply_waiting"  # The Queen replied in the chat.
    GOAL_COMPLETED = "goal_completed"  # A goal this device submitted finished.
    SECURITY_EVENT = "security_event"  # An Entrance security event, e.g. a device asks to join.
    WITHDRAWN = "withdrawn"  # The item a previous notice pointed at no longer needs the human.


class ChannelKind(Enum):
    """How a stored subscription is reached; the live WebSocket is not stored, so not listed."""

    WEBHOOK = "webhook"  # An Ed25519-signed HTTPS POST to a program's URL.
    WEB_PUSH = "web_push"  # RFC 8030 Web Push through the browser's or phone's push service.


class DeliveryOutcome(Enum):
    """How one delivery of one notice to one destination ended."""

    DELIVERED = "delivered"  # The destination accepted it (a 2xx, or a live frame sent).
    RETRY_LATER = "retry_later"  # A transient failure: network, timeout, 408, 429 or 5xx.
    GONE = "gone"  # Gone for good (a push service's 404/410; no live socket left): forget it.
    REFUSED = "refused"  # The destination guard refused where it points; nothing was sent.
    REJECTED = "rejected"  # The receiver answered with a final refusal (another 4xx, a 3xx).


class PushNotice(BaseModel):
    """One "something is waiting" notice: what kind, pointing at which item, minted when.

    Crosses every push channel as ``notice_json``: a webhook body, a Web Push plaintext, a
    WebSocket frame. The event id is minted once and reused by every retry and every channel, so
    a receiver dedupes on it.
    """

    model_config = _MODEL_CONFIG

    event_id: EventIdField = Field(
        description="The notice's id (event_ plus a ULID from the injected clock); stable "
        "across retries and channels, the receiver's idempotency key."
    )
    kind: NoticeKind = Field(description="What is waiting; never the item itself.")
    ref: str = Field(
        min_length=1,
        max_length=MAX_REF_CHARS,
        pattern=_REF_PATTERN,
        description="The id of the question, Alarm, goal or security event it points at.",
    )
    created_at: UtcDatetime = Field(description="When the notice was minted.")

    @classmethod
    def mint(cls, kind: NoticeKind, ref: str, clock: Clock) -> PushNotice:
        """Mint a notice with a fresh event id, stamped by ``clock``.

        Args:
            kind: What is waiting.
            ref: The id of the item it points at; at most ``MAX_REF_CHARS`` of ``[A-Za-z0-9_.:-]``.
            clock: Stamps the event id and ``created_at``.

        Returns:
            The notice.

        Raises:
            pydantic.ValidationError: ``ref`` is empty, too long or not an identifier.
        """
        return cls(event_id=new_event_id(clock), kind=kind, ref=ref, created_at=clock.now())


def notice_json(notice: PushNotice) -> bytes:
    """Render a notice as the exact bytes every channel sends: sorted keys, compact, UTF-8.

    Args:
        notice: The notice to send.

    Returns:
        The JSON object's bytes; the same notice always gives the same bytes.
    """
    document = notice.model_dump(mode="json")
    return json.dumps(document, sort_keys=True, separators=_JSON_SEPARATORS).encode("utf-8")


def _p256_point(value: str) -> str:
    """Accept a P-256 public key point (padded or not) and keep its canonical unpadded text."""
    text = value.rstrip("=")
    raw = b64url_decode(text)
    if len(raw) != P256_POINT_BYTES:
        raise ValueError(f"p256dh must decode to a {P256_POINT_BYTES}-byte uncompressed point.")
    # from_encoded_point refuses a point that is not on the curve: encryption would fail later.
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    return text


def _auth_secret(value: str) -> str:
    """Accept a 16-byte auth secret (padded or not) and keep its canonical unpadded text."""
    text = value.rstrip("=")
    if len(b64url_decode(text)) != AUTH_SECRET_BYTES:
        raise ValueError(f"auth must decode to {AUTH_SECRET_BYTES} bytes.")
    return text


class WebPushKeys(BaseModel):
    """A Web Push subscription's keys, as a browser's ``PushSubscription.toJSON()`` gives them.

    Crosses the subscribe route inbound and the subscription store; ``auth`` is a secret shared
    with the user agent, so it never appears in ``repr``, a log line or the trail.
    """

    model_config = _MODEL_CONFIG

    p256dh: Annotated[str, AfterValidator(_p256_point)] = Field(
        repr=False,
        description="The user agent's P-256 public key, base64url of the uncompressed point.",
    )
    auth: Annotated[str, AfterValidator(_auth_secret)] = Field(
        repr=False, description="The user agent's 16-byte auth secret, base64url."
    )


class Subscription(BaseModel):
    """One device's registered push destination: a webhook URL, or a Web Push endpoint and keys.

    Stored in the push tables; built only by the dispatcher after the registration gate passed.
    """

    model_config = _MODEL_CONFIG

    id: SubscriptionId = Field(
        pattern=_SUBSCRIPTION_ID_PATTERN, description="The subscription's id: sub_ plus a ULID."
    )
    device_id: DeviceIdField = Field(description="The enrolled device it delivers to.")
    channel: ChannelKind = Field(description="Which channel reaches it.")
    endpoint: str = Field(
        min_length=1,
        max_length=MAX_ENDPOINT_CHARS,
        repr=False,
        description="The webhook URL or the push service endpoint, as the device registered it.",
    )
    keys: WebPushKeys | None = Field(
        default=None, repr=False, description="The Web Push keys; None for a webhook."
    )
    created_at: UtcDatetime = Field(description="When it was registered.")

    @model_validator(mode="after")
    def _fits_its_channel(self) -> Subscription:
        """Require keys and https for Web Push, no keys for a webhook, and an http(s) URL."""
        if not self.endpoint.startswith(_ENDPOINT_SCHEMES):
            raise ValueError("A push endpoint is an absolute http or https URL.")
        if (self.keys is not None) != (self.channel is ChannelKind.WEB_PUSH):
            raise ValueError("A web_push subscription carries keys; a webhook carries none.")
        # RFC 8030 push services are https only, and browsers never hand out anything else.
        if self.channel is ChannelKind.WEB_PUSH and not self.endpoint.startswith("https://"):
            raise ValueError("A Web Push endpoint is always https.")
        return self


def new_subscription_id(clock: Clock) -> SubscriptionId:
    """Mint a subscription id: ``sub_`` and a ULID timestamped by ``clock``.

    Args:
        clock: Stamps the ULID, so ids sort by registration time.

    Returns:
        A fresh id with 80 random bits.
    """
    timestamp_ms = int(clock.now().timestamp() * _MILLISECONDS_PER_SECOND)
    ulid = encode_ulid(timestamp_ms, secrets.token_bytes(_ULID_RANDOM_BYTES))
    return SubscriptionId(f"{SUBSCRIPTION_ID_PREFIX}{ulid}")
