"""Deliver notices to browsers and phones through their push service, as RFC 8030 Web Push.

A browser (or a phone's UnifiedPush distributor, roadmap 12.12) subscribes with the Hive's VAPID
public key and registers the endpoint and keys it got back; ``WebPush`` then POSTs each notice to
that endpoint, encrypted so the push service cannot read it (RFC 8291, ``ece``), signed so the
push service knows it is the Hive (RFC 8292, ``vapid``), and shaped so the push service learns as
little as possible (ADR-0042): every plaintext is the notice JSON padded to exactly
``NOTICE_PADDED_BYTES`` before encryption, so every body has the same length whatever the kind;
the ``Topic`` is keyed (``topic``), so it lets a ``withdrawn`` notice replace an undelivered
original without being an identifier anyone else can compute. ``TTL`` and ``Urgency`` are set
per kind, and every kind that means "something is waiting" shares one value on purpose: headers
that differed by kind would tell the push service the kind the padding hides. Only ``withdrawn``
differs, which reveals nothing, since it reuses the original's ``Topic`` anyway. A 404 or 410
means the subscription is gone; 408, 429 and 5xx are worth retrying later; anything else is
final. Every delivery vets the endpoint again and connects only to the address it checked.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.web_push``. A
    ``PushChannel``, called by ``hivemind.entrance.push.dispatch.PushDispatcher``. Calls into the
    injected ``httpx.AsyncClient``, the destination guard, and this package's ``ece``, ``vapid``
    and ``topic``.

Key invariants:
    - Every body is ``HEADER_BYTES + NOTICE_PADDED_BYTES + TAG_BYTES`` bytes, for every notice.
    - Every message uses a fresh ephemeral key and salt, and a freshly signed VAPID JWT.
    - ``deliver`` never raises for a failed delivery and never logs the endpoint, the keys or
      the payload.

See Also:
    - RFC 8030 section 5 (TTL, Urgency, Topic), RFC 8291, RFC 8292.
    - docs/adr/0042-landing-board-versioning-and-push.md for the padding, Topic and header rules.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import MappingProxyType

import httpx

from hivemind.common.logging import get_logger
from hivemind.entrance.auth import b64url_decode
from hivemind.entrance.push.destinations import DestinationGuard, refusal_outcome
from hivemind.entrance.push.errors import DestinationRefusedError
from hivemind.entrance.push.models import (
    DeliveryOutcome,
    NoticeKind,
    PushNotice,
    Subscription,
    WebPushKeys,
    notice_json,
)
from hivemind.entrance.push.web_push.ece import ReceiverKeys, encrypt
from hivemind.entrance.push.web_push.topic import topic_for
from hivemind.entrance.push.web_push.vapid import VapidSigner

NOTICE_PADDED_BYTES = 512  # ADR-0042: every plaintext is padded to this, so lengths reveal nothing.
WEB_PUSH_TIMEOUT_S = 10.0  # One POST to a push service; they answer in well under a second.
WAITING_TTL_S = 24 * 60 * 60  # A push service holds a "something is waiting" notice for a day.
# A withdrawal must still reach a phone that was off for days, to clear the notice it shows.
WITHDRAWN_TTL_S = 7 * 24 * 60 * 60
URGENCY_WAITING = "high"  # RFC 8030 section 5.3: delivered even on low battery; a human is wanted.
URGENCY_WITHDRAWN = "normal"  # Clearing a stale notice can wait for the device's normal rhythm.
HTTP_REQUEST_TIMEOUT = 408  # The push service timed out reading the request: try again later.
HTTP_NOT_FOUND = 404  # RFC 8030 section 7.3: the subscription expired or never existed.
HTTP_GONE = 410  # RFC 8030 section 7.3: the user agent unsubscribed.
HTTP_TOO_MANY_REQUESTS = 429  # RFC 8030 section 8.4: the push service is rate limiting.
_SUCCESS = range(200, 300)  # 201 Created is RFC 8030's answer; any 2xx means accepted.
_SERVER_ERROR_FLOOR = 500  # A 5xx is the push service's own, usually transient, trouble.
_CONTENT_HEADERS = {  # RFC 8291 section 4: an aes128gcm body.
    "Content-Type": "application/octet-stream",
    "Content-Encoding": "aes128gcm",
}

# Every kind that says "something is waiting" shares one value (module docstring).
TTL_BY_KIND: Mapping[NoticeKind, int] = MappingProxyType(
    {
        NoticeKind.QUESTION_WAITING: WAITING_TTL_S,
        NoticeKind.ALARM_WAITING: WAITING_TTL_S,
        NoticeKind.REPLY_WAITING: WAITING_TTL_S,
        NoticeKind.GOAL_COMPLETED: WAITING_TTL_S,
        NoticeKind.SECURITY_EVENT: WAITING_TTL_S,
        NoticeKind.WITHDRAWN: WITHDRAWN_TTL_S,
    }
)
URGENCY_BY_KIND: Mapping[NoticeKind, str] = MappingProxyType(
    {
        NoticeKind.QUESTION_WAITING: URGENCY_WAITING,
        NoticeKind.ALARM_WAITING: URGENCY_WAITING,
        NoticeKind.REPLY_WAITING: URGENCY_WAITING,
        NoticeKind.GOAL_COMPLETED: URGENCY_WAITING,
        NoticeKind.SECURITY_EVENT: URGENCY_WAITING,
        NoticeKind.WITHDRAWN: URGENCY_WITHDRAWN,
    }
)

log = get_logger(__name__)

__all__ = [
    "NOTICE_PADDED_BYTES",
    "TTL_BY_KIND",
    "URGENCY_BY_KIND",
    "WEB_PUSH_TIMEOUT_S",
    "WebPush",
    "web_push_outcome",
]


class WebPush:
    """The Web Push ``PushChannel``: encrypted, VAPID-signed, padded, keyed-Topic deliveries."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        vapid: VapidSigner,
        topic_key: bytes,
        guard: DestinationGuard,
    ) -> None:
        """Build the channel.

        Args:
            client: The HTTP client every delivery uses; built by the composition root without
                environment proxies, so the guard's pinned address is where it connects.
            vapid: Signs each request's ``Authorization`` header.
            topic_key: The Hive's Topic key (``load_or_mint_topic_key``); a secret.
            guard: Vets the endpoint before every delivery.
        """
        self._client = client
        self._vapid = vapid
        self._topic_key = topic_key
        self._guard = guard

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """Encrypt ``notice`` for the subscription's user agent and hand it to its push service.

        Args:
            notice: The notice.
            subscription: A web_push subscription.

        Returns:
            DELIVERED on a 2xx; GONE on 404 or 410; RETRY_LATER on 408, 429, a 5xx or a network
            failure; REFUSED when the guard refuses the endpoint; REJECTED otherwise.
        """
        # A webhook subscription carries no keys: nothing could decrypt it, so never send it.
        if subscription.keys is None:
            return DeliveryOutcome.REJECTED
        try:
            vetted = await self._guard.vet(subscription.endpoint)
        except DestinationRefusedError as refusal:
            log.debug("push.web_push_refused", subscription_id=subscription.id)
            return refusal_outcome(refusal)
        body = encrypt(notice_json(notice), _receiver(subscription.keys), NOTICE_PADDED_BYTES)
        headers = {
            **vetted.headers(),
            **_CONTENT_HEADERS,
            **self._push_headers(notice, subscription),
        }
        try:
            # Latency: one HTTPS round trip to a push service; on timeout, retried later.
            async with (
                asyncio.timeout(WEB_PUSH_TIMEOUT_S),
                self._client.stream(
                    "POST",
                    vetted.request_url(),
                    content=body,
                    headers=headers,
                    extensions=vetted.extensions(),
                    follow_redirects=False,
                    timeout=WEB_PUSH_TIMEOUT_S,
                ) as response,
            ):
                # Only the status matters; a push service's body is diagnostics we never log.
                status = response.status_code
        except (httpx.HTTPError, TimeoutError):
            return DeliveryOutcome.RETRY_LATER
        return web_push_outcome(status)

    def _push_headers(self, notice: PushNotice, subscription: Subscription) -> dict[str, str]:
        """Build RFC 8030's per-message headers and the VAPID authorisation for one delivery."""
        return {
            "TTL": str(TTL_BY_KIND[notice.kind]),
            "Urgency": URGENCY_BY_KIND[notice.kind],
            "Topic": topic_for(self._topic_key, notice.ref),
            "Authorization": self._vapid.authorization(subscription.endpoint),
        }


def web_push_outcome(status: int) -> DeliveryOutcome:
    """Map a push service's HTTP status to how the delivery ended.

    Args:
        status: The response's status code.

    Returns:
        DELIVERED for a 2xx; GONE for 404 or 410; RETRY_LATER for 408, 429 or a 5xx; REJECTED
        for anything else (a bad VAPID token, a payload refused, a redirect never followed).
    """
    if status in _SUCCESS:
        return DeliveryOutcome.DELIVERED
    if status in (HTTP_NOT_FOUND, HTTP_GONE):
        return DeliveryOutcome.GONE
    if status in (HTTP_REQUEST_TIMEOUT, HTTP_TOO_MANY_REQUESTS) or status >= _SERVER_ERROR_FLOOR:
        return DeliveryOutcome.RETRY_LATER
    return DeliveryOutcome.REJECTED


def _receiver(keys: WebPushKeys) -> ReceiverKeys:
    """Decode a subscription's stored keys into the raw bytes the encryption takes."""
    return ReceiverKeys(public_key=b64url_decode(keys.p256dh), auth_secret=b64url_decode(keys.auth))
