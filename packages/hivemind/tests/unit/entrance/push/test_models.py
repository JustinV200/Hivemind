"""Tests for hivemind.entrance.push.models: the notice, the subscription and their rules.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.models for the module under test.
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError
from unit.entrance.push.support import (
    HOOK_URL,
    PUSH_URL,
    SteppingClock,
    UserAgent,
    web_push_subscription,
    webhook_subscription,
)

from hivemind.entrance.auth import b64url_encode
from hivemind.entrance.push import (
    ChannelKind,
    NoticeKind,
    PushNotice,
    Subscription,
    WebPushKeys,
    new_subscription_id,
    notice_json,
)
from hivemind.entrance.push.models import MAX_REF_CHARS
from hivemind.entrance.push.web_push import NOTICE_PADDED_BYTES

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # A question's id.
_SUBSCRIPTION_ID = re.compile(r"sub_[0-9A-HJKMNP-TV-Z]{26}")  # sub_ and a Crockford ULID.


def test_mint_stamps_a_fresh_event_id_and_the_clock_time() -> None:
    clock = SteppingClock()

    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock)

    assert notice.event_id.startswith("event_")
    assert notice.created_at == clock.now()
    assert notice.kind is NoticeKind.QUESTION_WAITING
    assert notice.ref == _REF
    assert PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, clock).event_id != notice.event_id


def test_a_notice_has_no_content_field() -> None:
    assert set(PushNotice.model_fields) == {"event_id", "kind", "ref", "created_at"}
    with pytest.raises(ValidationError):
        PushNotice.model_validate(
            {
                **json.loads(
                    notice_json(PushNotice.mint(NoticeKind.ALARM_WAITING, _REF, SteppingClock()))
                ),
                "content": "the question text",
            }
        )


def test_notice_json_is_sorted_compact_and_round_trips() -> None:
    notice = PushNotice.mint(NoticeKind.REPLY_WAITING, _REF, SteppingClock())

    body = notice_json(notice)

    assert body.startswith(b'{"created_at":')
    assert b" " not in body
    assert list(json.loads(body)) == ["created_at", "event_id", "kind", "ref"]
    assert PushNotice.model_validate_json(body) == notice


def test_the_longest_notice_fits_the_padded_web_push_record() -> None:
    notice = PushNotice.mint(NoticeKind.QUESTION_WAITING, "x" * MAX_REF_CHARS, SteppingClock())

    assert len(notice_json(notice)) < NOTICE_PADDED_BYTES


@pytest.mark.parametrize(
    "ref", ["", "x" * (MAX_REF_CHARS + 1), "msg_1\nX-Injected: 1", "what is 2 + 2?", "-leading"]
)
def test_a_notice_refuses_a_ref_that_is_not_a_bounded_identifier(ref: str) -> None:
    with pytest.raises(ValidationError):
        PushNotice.mint(NoticeKind.QUESTION_WAITING, ref, SteppingClock())


def test_subscriptions_round_trip_through_json() -> None:
    clock = SteppingClock()
    subscriptions = [webhook_subscription(clock), web_push_subscription(clock, UserAgent())]

    for subscription in subscriptions:
        assert Subscription.model_validate_json(subscription.model_dump_json()) == subscription


def test_a_web_push_subscription_needs_keys_and_https() -> None:
    clock = SteppingClock()
    base = webhook_subscription(clock).model_dump()

    with pytest.raises(ValidationError, match="carries keys"):
        Subscription.model_validate({**base, "channel": ChannelKind.WEB_PUSH})
    with pytest.raises(ValidationError, match="always https"):
        Subscription.model_validate(
            {
                **base,
                "channel": ChannelKind.WEB_PUSH,
                "endpoint": "http://push.example.net/x",
                "keys": UserAgent().keys,
            }
        )


def test_a_webhook_subscription_carries_no_keys_and_an_http_url() -> None:
    base = webhook_subscription(SteppingClock()).model_dump()

    with pytest.raises(ValidationError, match="carries none"):
        Subscription.model_validate({**base, "keys": UserAgent().keys})
    with pytest.raises(ValidationError, match="http or https"):
        Subscription.model_validate({**base, "endpoint": "file:///etc/passwd"})


def test_repr_shows_neither_the_endpoint_nor_the_keys() -> None:
    agent = UserAgent()
    subscription = web_push_subscription(SteppingClock(), agent)

    text = repr(subscription)

    assert PUSH_URL not in text
    assert agent.keys.auth not in text
    assert agent.keys.p256dh not in text
    assert HOOK_URL not in repr(webhook_subscription(SteppingClock()))
    assert subscription.id in text


def test_web_push_keys_accept_padded_values_and_keep_the_canonical_form() -> None:
    keys = UserAgent().keys

    padded = WebPushKeys(p256dh=keys.p256dh + "=", auth=keys.auth + "==")

    assert padded == keys


@pytest.mark.parametrize(
    ("p256dh", "auth"),
    [
        (b64url_encode(b"\x04" + bytes(63)), b64url_encode(bytes(16))),  # 64 bytes, not 65.
        (b64url_encode(b"\x04" + bytes(64)), b64url_encode(bytes(16))),  # Not on the curve.
        (None, b64url_encode(bytes(15))),  # Auth secret one byte short.
    ],
)
def test_web_push_keys_refuse_what_cannot_encrypt(p256dh: str | None, auth: str) -> None:
    with pytest.raises(ValidationError):
        WebPushKeys(p256dh=p256dh or UserAgent().keys.p256dh, auth=auth)


def test_new_subscription_ids_are_distinct_prefixed_ulids() -> None:
    clock = SteppingClock()

    ids = {new_subscription_id(clock) for _ in range(50)}

    assert len(ids) == 50
    assert all(_SUBSCRIPTION_ID.fullmatch(subscription_id) for subscription_id in ids)
