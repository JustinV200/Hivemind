"""Deliver notices to programs as HTTPS POSTs signed with the Hive's key, retried with backoff.

A program device (a home automation hub, another agent framework, a script) registers a URL and
receives each notice there as JSON (ADR-0034). The Hive signs every delivery with its own Ed25519
key over ``hivemind.entrance.auth.canonical.webhook_string``: the subscription id, the event id, a
timestamp and the body's SHA-256, sent as ``X-Hive-Signature`` (base64url) beside
``X-Hive-Timestamp`` (integer Unix seconds) and ``X-Hive-Event-Id``. WHY every attempt is signed
afresh: a retry then never arrives with a stale timestamp a receiver would reject, while the event
id stays the same so the receiver dedupes; and because the subscription id is signed, a notice
captured at one receiver cannot be replayed to another. A network error, a 5xx, 408 or 429 is
retried with exponential backoff (sleeping on the injected clock); any other 4xx, and any
redirect, is final, and redirects are never followed. Every attempt vets the URL again through the
destination guard and connects only to the address it checked.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. A
    ``PushChannel``, called by ``hivemind.entrance.push.dispatch.PushDispatcher``. Calls into the
    injected ``httpx.AsyncClient``, the Hive's ``Ed25519Signer``, the destination guard, and
    ``hivemind.entrance.auth.canonical`` for the signed string.

Key invariants:
    - Every attempt carries a fresh timestamp and a fresh signature over the exact bytes sent.
    - At most ``WEBHOOK_ATTEMPTS`` requests per delivery, each bounded by ``WEBHOOK_TIMEOUT_S``.
    - ``deliver`` never raises for a failed delivery, and never logs the URL or the body.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the webhook contract.
    - hivemind.entrance.auth.canonical.webhook_string for the signed string.
"""

from __future__ import annotations

import asyncio
import base64

import httpx

from hivemind.common.logging import get_logger
from hivemind.entrance.auth import b64url_encode, sha256_hex, webhook_string
from hivemind.entrance.push.destinations import DestinationGuard, refusal_outcome
from hivemind.entrance.push.errors import DestinationRefusedError
from hivemind.entrance.push.models import DeliveryOutcome, PushNotice, Subscription, notice_json
from waggle.clock import Clock
from waggle.signing import Ed25519Signer

WEBHOOK_ATTEMPTS = 5  # One try and four retries: about half a minute of backoff, then give up.
WEBHOOK_BACKOFF_BASE_S = 2.0  # The first retry waits 2 s, then 4, 8, 16: doubling each time.
WEBHOOK_BACKOFF_CAP_S = 60.0  # No single wait exceeds a minute, whatever the attempt count.
WEBHOOK_TIMEOUT_S = 10.0  # One attempt's whole budget: connect, send, read the status line.
HTTP_REQUEST_TIMEOUT = 408  # The receiver timed out reading the request: worth another try.
HTTP_TOO_MANY_REQUESTS = 429  # The receiver is shedding load: back off and try again.
_SUCCESS = range(200, 300)  # Any 2xx: the receiver accepted the notice.
_SERVER_ERROR_FLOOR = 500  # A 5xx is the receiver's own trouble, usually transient.

log = get_logger(__name__)

__all__ = [
    "WEBHOOK_ATTEMPTS",
    "WEBHOOK_BACKOFF_BASE_S",
    "WEBHOOK_BACKOFF_CAP_S",
    "WEBHOOK_TIMEOUT_S",
    "WebhookPush",
    "backoff_delay",
    "webhook_outcome",
]


class WebhookPush:
    """The webhook ``PushChannel``: signed POSTs of the notice JSON, retried with backoff."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        signer: Ed25519Signer,
        guard: DestinationGuard,
        clock: Clock,
    ) -> None:
        """Build the channel.

        Args:
            client: The HTTP client every delivery uses; built by the composition root without
                environment proxies, so the guard's pinned address is where it connects.
            signer: The Hive's own Ed25519 key (``hive.ed25519``), whose public half a program
                pins at approval.
            guard: Vets the URL before every attempt.
            clock: Stamps each signature and paces the backoff sleeps.
        """
        self._client = client
        self._signer = signer
        self._guard = guard
        self._clock = clock

    async def deliver(self, notice: PushNotice, subscription: Subscription) -> DeliveryOutcome:
        """POST ``notice`` to the subscription's URL, retrying transient failures with backoff.

        Args:
            notice: The notice; its event id is the same on every attempt.
            subscription: A webhook subscription.

        Returns:
            DELIVERED on a 2xx; REJECTED on any other final answer; REFUSED when the guard
            refuses the URL; RETRY_LATER once every attempt failed transiently.
        """
        body = notice_json(notice)
        digest = sha256_hex(body)
        attempt = 1
        # Each pass is one signed attempt; only a transient failure earns another, after a wait.
        while True:
            outcome = await self._attempt(notice, subscription, body, digest)
            if outcome is not DeliveryOutcome.RETRY_LATER or attempt >= WEBHOOK_ATTEMPTS:
                log.debug(
                    "push.webhook_done", subscription_id=subscription.id, outcome=outcome.value
                )
                return outcome
            # Latency: the backoff itself; FakeClock makes it instant in tests.
            await self._clock.sleep(backoff_delay(attempt))
            attempt += 1

    async def _attempt(
        self, notice: PushNotice, subscription: Subscription, body: bytes, digest: str
    ) -> DeliveryOutcome:
        """Vet, sign and send one attempt, mapping every failure to an outcome."""
        try:
            vetted = await self._guard.vet(subscription.endpoint)
        except DestinationRefusedError as refusal:
            # The reason only: the URL may carry a receiver's token.
            log.debug(
                "push.webhook_refused", subscription_id=subscription.id, reason=refusal.reason.value
            )
            return refusal_outcome(refusal)
        headers = {
            **vetted.headers(),
            "Content-Type": "application/json",
            "X-Hive-Event-Id": notice.event_id,
            **self._signature_headers(subscription, notice, digest),
        }
        try:
            # Latency: one HTTPS round trip to a device's receiver; on timeout it is retried.
            async with (
                asyncio.timeout(WEBHOOK_TIMEOUT_S),
                self._client.stream(
                    "POST",
                    vetted.request_url(),
                    content=body,
                    headers=headers,
                    extensions=vetted.extensions(),
                    follow_redirects=False,
                    timeout=WEBHOOK_TIMEOUT_S,
                ) as response,
            ):
                # Only the status matters; the body is never read, so a huge one costs nothing.
                status = response.status_code
        except (httpx.HTTPError, TimeoutError):
            return DeliveryOutcome.RETRY_LATER
        return webhook_outcome(status)

    def _signature_headers(
        self, subscription: Subscription, notice: PushNotice, digest: str
    ) -> dict[str, str]:
        """Sign this attempt now: a fresh timestamp and the Hive's signature over the string."""
        timestamp = int(self._clock.now().timestamp())
        message = webhook_string(subscription.id, notice.event_id, timestamp, digest)
        # Ed25519Signer answers in padded standard base64 (waggle's wire form); the Entrance's
        # one encoding is unpadded base64url, so the raw 64 bytes are re-encoded.
        raw_signature = base64.b64decode(self._signer.sign(message))
        return {
            "X-Hive-Timestamp": str(timestamp),
            "X-Hive-Signature": b64url_encode(raw_signature),
        }


def webhook_outcome(status: int) -> DeliveryOutcome:
    """Map a receiver's HTTP status to how the delivery ended.

    Args:
        status: The response's status code.

    Returns:
        DELIVERED for a 2xx; RETRY_LATER for 408, 429 or a 5xx; REJECTED for anything else (a
        3xx is final too, since redirects are never followed).
    """
    if status in _SUCCESS:
        return DeliveryOutcome.DELIVERED
    if status in (HTTP_REQUEST_TIMEOUT, HTTP_TOO_MANY_REQUESTS) or status >= _SERVER_ERROR_FLOOR:
        return DeliveryOutcome.RETRY_LATER
    return DeliveryOutcome.REJECTED


def backoff_delay(attempt: int) -> float:
    """Return how long to wait after failed attempt number ``attempt`` (1-based).

    Args:
        attempt: The attempt that just failed; at least 1.

    Returns:
        ``WEBHOOK_BACKOFF_BASE_S`` doubled per earlier attempt, never above the cap.
    """
    return min(WEBHOOK_BACKOFF_BASE_S * 2.0 ** (attempt - 1), WEBHOOK_BACKOFF_CAP_S)
