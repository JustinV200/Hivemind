"""Tests for hivemind.entrance.push.web_push.channel: WebPush, the RFC 8030 channel.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/web_push/channel.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.web_push.channel for the module under test.
"""

from __future__ import annotations

import secrets

import httpx
import pytest
from unit.entrance.push.support import (
    PUSH_ADDRESS,
    PUSH_HOST,
    PUSH_URL,
    Recorder,
    StaticResolver,
    SteppingClock,
    UserAgent,
    make_guard,
    web_push_subscription,
    webhook_subscription,
)

from hivemind.entrance.push import (
    DeliveryOutcome,
    NoticeKind,
    PushNotice,
    Subscription,
    notice_json,
)
from hivemind.entrance.push.web_push import (
    HEADER_BYTES,
    NOTICE_PADDED_BYTES,
    TAG_BYTES,
    TTL_BY_KIND,
    URGENCY_BY_KIND,
    VapidKey,
    VapidSigner,
    WebPush,
    topic_for,
    vapid_audience,
)

_TOPIC_KEY = secrets.token_bytes(32)  # This test module's Topic key.
_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question every notice here points at.
_BODY_BYTES = HEADER_BYTES + NOTICE_PADDED_BYTES + TAG_BYTES  # 614: every Web Push body.


async def _deliver(
    recorder: Recorder,
    notice: PushNotice,
    subscription: Subscription,
    resolver: StaticResolver | None = None,
) -> DeliveryOutcome:
    """Deliver one notice through a WebPush whose HTTP goes to ``recorder``."""
    signer = VapidSigner(VapidKey.generate(), "mailto:operator@example.net", SteppingClock())
    async with recorder.client() as client:
        channel = WebPush(client, signer, _TOPIC_KEY, make_guard(resolver))
        return await channel.deliver(notice, subscription)


def _notice(kind: NoticeKind = NoticeKind.QUESTION_WAITING, ref: str = _REF) -> PushNotice:
    """Mint a notice at the test's fixed time."""
    return PushNotice.mint(kind, ref, SteppingClock())


async def test_deliver_posts_a_body_only_the_user_agent_can_open() -> None:
    agent = UserAgent()
    recorder = Recorder()
    notice = _notice()

    outcome = await _deliver(recorder, notice, web_push_subscription(SteppingClock(), agent))

    assert outcome is DeliveryOutcome.DELIVERED
    body = recorder.requests[0].content
    assert agent.open(body) == notice_json(notice)
    assert notice_json(notice) not in body


async def test_deliver_sends_the_rfc_8030_and_8291_headers() -> None:
    recorder = Recorder()
    notice = _notice()

    await _deliver(recorder, notice, web_push_subscription(SteppingClock(), UserAgent()))

    headers = recorder.requests[0].headers
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["content-type"] == "application/octet-stream"
    assert headers["ttl"] == str(TTL_BY_KIND[NoticeKind.QUESTION_WAITING])
    assert headers["urgency"] == "high"
    assert headers["topic"] == topic_for(_TOPIC_KEY, _REF)
    assert headers["authorization"].startswith("vapid t=")


async def test_deliver_connects_to_the_checked_address_keeping_the_name() -> None:
    recorder = Recorder()

    await _deliver(recorder, _notice(), web_push_subscription(SteppingClock(), UserAgent()))

    request = recorder.requests[0]
    assert request.url.host == PUSH_ADDRESS
    assert request.url.raw_path == b"/wpush/v2/gAAAAABm-subscription"
    assert request.headers["host"] == PUSH_HOST
    assert request.headers["connection"] == "close"
    assert request.extensions["sni_hostname"] == PUSH_HOST
    assert vapid_audience(PUSH_URL) == f"https://{PUSH_HOST}"


async def test_every_kind_and_ref_length_gives_one_ciphertext_length() -> None:
    # ADR-0034: the push service must not learn the kind from the length.
    agent = UserAgent()
    recorder = Recorder()
    subscription = web_push_subscription(SteppingClock(), agent)
    refs = ("a", _REF, "x" * 128)

    for kind in NoticeKind:
        for ref in refs:
            await _deliver(recorder, _notice(kind, ref), subscription)

    lengths = {len(request.content) for request in recorder.requests}
    assert len(recorder.requests) == len(NoticeKind) * len(refs)
    assert lengths == {_BODY_BYTES}


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (201, DeliveryOutcome.DELIVERED),
        (202, DeliveryOutcome.DELIVERED),
        (404, DeliveryOutcome.GONE),
        (410, DeliveryOutcome.GONE),
        (408, DeliveryOutcome.RETRY_LATER),
        (429, DeliveryOutcome.RETRY_LATER),
        (500, DeliveryOutcome.RETRY_LATER),
        (503, DeliveryOutcome.RETRY_LATER),
        (400, DeliveryOutcome.REJECTED),
        (403, DeliveryOutcome.REJECTED),
        (413, DeliveryOutcome.REJECTED),
        (302, DeliveryOutcome.REJECTED),
    ],
)
async def test_deliver_maps_the_push_service_status(status: int, outcome: DeliveryOutcome) -> None:
    recorder = Recorder(status)

    result = await _deliver(
        recorder, _notice(), web_push_subscription(SteppingClock(), UserAgent())
    )

    assert result is outcome
    assert len(recorder.requests) == 1


async def test_deliver_retries_later_on_a_network_error() -> None:
    recorder = Recorder(httpx.ConnectError("connection refused"))

    result = await _deliver(
        recorder, _notice(), web_push_subscription(SteppingClock(), UserAgent())
    )

    assert result is DeliveryOutcome.RETRY_LATER


async def test_deliver_refuses_an_endpoint_that_now_resolves_to_loopback() -> None:
    # Registered while it pointed at a push service; its name now points at the Hive Stand.
    resolver = StaticResolver()
    resolver.answer(PUSH_HOST, "127.0.0.1")
    recorder = Recorder()

    result = await _deliver(
        recorder, _notice(), web_push_subscription(SteppingClock(), UserAgent()), resolver
    )

    assert result is DeliveryOutcome.REFUSED
    assert recorder.requests == []


async def test_deliver_retries_later_when_the_endpoint_stops_resolving() -> None:
    resolver = StaticResolver()
    resolver.answer(PUSH_HOST)
    recorder = Recorder()

    result = await _deliver(
        recorder, _notice(), web_push_subscription(SteppingClock(), UserAgent()), resolver
    )

    assert result is DeliveryOutcome.RETRY_LATER
    assert recorder.requests == []


async def test_deliver_rejects_a_subscription_without_keys() -> None:
    recorder = Recorder()

    result = await _deliver(recorder, _notice(), webhook_subscription(SteppingClock()))

    assert result is DeliveryOutcome.REJECTED
    assert recorder.requests == []


def test_every_waiting_kind_shares_one_ttl_and_one_urgency() -> None:
    waiting = [kind for kind in NoticeKind if kind is not NoticeKind.WITHDRAWN]

    assert len({TTL_BY_KIND[kind] for kind in waiting}) == 1
    assert len({URGENCY_BY_KIND[kind] for kind in waiting}) == 1
    assert TTL_BY_KIND[NoticeKind.WITHDRAWN] >= TTL_BY_KIND[NoticeKind.QUESTION_WAITING]
    assert set(TTL_BY_KIND) == set(NoticeKind) == set(URGENCY_BY_KIND)
