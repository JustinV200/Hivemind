"""Tests for hivemind.entrance.push.webhook: signed webhook delivery, retries and refusals.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/webhook.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.webhook for the module under test.
"""

from __future__ import annotations

import httpx
import pytest
from unit.entrance.push.support import (
    HOOK_ADDRESS,
    HOOK_HOST,
    Recorder,
    StaticResolver,
    SteppingClock,
    make_guard,
    webhook_subscription,
)

from hivemind.entrance.auth import b64url_decode, sha256_hex, verify_ed25519, webhook_string
from hivemind.entrance.push import (
    DeliveryOutcome,
    NoticeKind,
    PushNotice,
    Subscription,
    WebhookPush,
    notice_json,
)
from hivemind.entrance.push.webhook import (
    WEBHOOK_ATTEMPTS,
    WEBHOOK_BACKOFF_BASE_S,
    WEBHOOK_BACKOFF_CAP_S,
    backoff_delay,
    webhook_outcome,
)
from waggle.signing import Ed25519Signer

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question every notice here points at.


async def _deliver(
    recorder: Recorder,
    clock: SteppingClock,
    subscription: Subscription | None = None,
    resolver: StaticResolver | None = None,
) -> tuple[DeliveryOutcome, PushNotice, Ed25519Signer, Subscription]:
    """Deliver one question notice through a WebhookPush whose HTTP goes to ``recorder``."""
    signer = Ed25519Signer.generate()
    target = subscription or webhook_subscription(clock)
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock)
    async with recorder.client() as client:
        channel = WebhookPush(client, signer, make_guard(resolver), clock)
        outcome = await channel.deliver(notice, target)
    return outcome, notice, signer, target


def _verifies(request: httpx.Request, signer: Ed25519Signer, subscription: Subscription) -> bool:
    """Check a delivered request's signature against the canonical string, as a receiver would."""
    message = webhook_string(
        subscription.id,
        request.headers["x-hive-event-id"],
        int(request.headers["x-hive-timestamp"]),
        sha256_hex(request.content),
    )
    signature = b64url_decode(request.headers["x-hive-signature"])
    return verify_ed25519(signer.public_key_bytes, message, signature)


async def test_deliver_posts_the_notice_json_signed_by_the_hive_key() -> None:
    clock = SteppingClock()
    recorder = Recorder(204)

    outcome, notice, signer, subscription = await _deliver(recorder, clock)

    request = recorder.requests[0]
    assert outcome is DeliveryOutcome.DELIVERED
    assert request.method == "POST"
    assert request.content == notice_json(notice)
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-hive-event-id"] == notice.event_id
    assert request.headers["x-hive-timestamp"] == str(int(clock.now().timestamp()))
    assert _verifies(request, signer, subscription)


async def test_signature_fails_for_another_subscription_or_an_altered_body() -> None:
    clock = SteppingClock()
    recorder = Recorder(200)

    _, _, signer, subscription = await _deliver(recorder, clock)

    request = recorder.requests[0]
    other = webhook_subscription(clock, subscription.device_id)
    altered = httpx.Request("POST", request.url, headers=request.headers, content=b"{}")
    assert not _verifies(request, signer, other)
    assert not _verifies(altered, signer, subscription)
    assert not _verifies(request, Ed25519Signer.generate(), subscription)


async def test_deliver_connects_to_the_checked_address_keeping_the_name() -> None:
    recorder = Recorder(200)

    await _deliver(recorder, SteppingClock())

    request = recorder.requests[0]
    assert request.url.host == HOOK_ADDRESS
    assert request.url.raw_path == b"/hive/notices?token=t0k3n"
    assert request.headers["host"] == HOOK_HOST
    assert request.extensions["sni_hostname"] == HOOK_HOST


async def test_retries_re_sign_with_a_new_timestamp_and_stop_on_a_final_4xx() -> None:
    clock = SteppingClock()
    recorder = Recorder(503, 429, 400)

    outcome, notice, signer, subscription = await _deliver(recorder, clock)

    assert outcome is DeliveryOutcome.REJECTED
    assert len(recorder.requests) == 3
    timestamps = [int(request.headers["x-hive-timestamp"]) for request in recorder.requests]
    assert timestamps == sorted(set(timestamps))
    assert {request.headers["x-hive-event-id"] for request in recorder.requests} == {
        notice.event_id
    }
    assert all(_verifies(request, signer, subscription) for request in recorder.requests)
    assert clock.sleeps == [WEBHOOK_BACKOFF_BASE_S, WEBHOOK_BACKOFF_BASE_S * 2]


@pytest.mark.parametrize(
    "failure", [500, 502, 408, 429, httpx.ConnectError("refused"), httpx.ReadTimeout("slow")]
)
async def test_a_transient_failure_is_retried_until_success(failure: int | Exception) -> None:
    recorder = Recorder(failure, 200)

    outcome, *_ = await _deliver(recorder, SteppingClock())

    assert outcome is DeliveryOutcome.DELIVERED
    assert len(recorder.requests) == 2


async def test_deliver_gives_up_after_the_last_attempt() -> None:
    clock = SteppingClock()
    recorder = Recorder(default=503)

    outcome, *_ = await _deliver(recorder, clock)

    assert outcome is DeliveryOutcome.RETRY_LATER
    assert len(recorder.requests) == WEBHOOK_ATTEMPTS
    assert len(clock.sleeps) == WEBHOOK_ATTEMPTS - 1


async def test_deliver_never_follows_a_redirect() -> None:
    recorder = Recorder(302)

    outcome, *_ = await _deliver(recorder, SteppingClock())

    assert outcome is DeliveryOutcome.REJECTED
    assert len(recorder.requests) == 1


async def test_deliver_refuses_a_url_that_now_resolves_to_loopback_without_sending() -> None:
    # Vetted at registration; its name was re-pointed at the Hive Stand's loopback since.
    resolver = StaticResolver()
    resolver.answer(HOOK_HOST, "127.0.0.1")
    recorder = Recorder(200)

    outcome, *_ = await _deliver(recorder, SteppingClock(), resolver=resolver)

    assert outcome is DeliveryOutcome.REFUSED
    assert recorder.requests == []


async def test_deliver_retries_a_name_that_stops_resolving_then_retries_later() -> None:
    resolver = StaticResolver()
    resolver.answer(HOOK_HOST)
    recorder = Recorder(200)

    outcome, *_ = await _deliver(recorder, SteppingClock(), resolver=resolver)

    assert outcome is DeliveryOutcome.RETRY_LATER
    assert recorder.requests == []
    assert resolver.lookups == [HOOK_HOST] * WEBHOOK_ATTEMPTS


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (200, DeliveryOutcome.DELIVERED),
        (204, DeliveryOutcome.DELIVERED),
        (301, DeliveryOutcome.REJECTED),
        (400, DeliveryOutcome.REJECTED),
        (401, DeliveryOutcome.REJECTED),
        (404, DeliveryOutcome.REJECTED),
        (410, DeliveryOutcome.REJECTED),
        (408, DeliveryOutcome.RETRY_LATER),
        (429, DeliveryOutcome.RETRY_LATER),
        (500, DeliveryOutcome.RETRY_LATER),
        (599, DeliveryOutcome.RETRY_LATER),
    ],
)
def test_webhook_outcome_maps_each_status(status: int, outcome: DeliveryOutcome) -> None:
    assert webhook_outcome(status) is outcome


def test_backoff_doubles_from_the_base_and_stops_at_the_cap() -> None:
    delays = [backoff_delay(attempt) for attempt in range(1, 10)]

    assert delays[:4] == [2.0, 4.0, 8.0, 16.0]
    assert max(delays) == WEBHOOK_BACKOFF_CAP_S
    assert delays == sorted(delays)
