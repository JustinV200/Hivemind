"""Tests for hivemind.entrance.push.web_push.topic: the keyed Web Push Topic and its key.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/web_push/topic.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.web_push.topic for the module under test.
"""

from __future__ import annotations

import hashlib
import re
import secrets

import pytest

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.auth import b64url_encode
from hivemind.entrance.push import PushConfigError
from hivemind.entrance.push.web_push import (
    TOPIC_CHARS,
    TOPIC_KEY_BYTES,
    TOPIC_KEY_NAME,
    load_or_mint_topic_key,
    topic_for,
)

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # A question's id, as a notice would point at it.
_RFC_8030_TOPIC = re.compile(r"[A-Za-z0-9_-]{32}")  # 32 characters of the URL-safe alphabet.


def test_topic_is_32_url_safe_characters() -> None:
    topic = topic_for(secrets.token_bytes(TOPIC_KEY_BYTES), _REF)

    assert TOPIC_CHARS == 32
    assert _RFC_8030_TOPIC.fullmatch(topic) is not None


def test_topic_is_stable_for_one_key_and_ref() -> None:
    key = secrets.token_bytes(TOPIC_KEY_BYTES)

    assert topic_for(key, _REF) == topic_for(key, _REF)


def test_topic_differs_between_refs() -> None:
    key = secrets.token_bytes(TOPIC_KEY_BYTES)

    assert topic_for(key, _REF) != topic_for(key, "alarm_01J8ZQ7X9K3M2N4P5Q6R7S8T9V")


def test_topic_cannot_be_derived_without_the_key() -> None:
    # Neither another Hive's key nor the unkeyed hash of the ref gives the same Topic.
    key = secrets.token_bytes(TOPIC_KEY_BYTES)
    unkeyed = b64url_encode(hashlib.sha256(_REF.encode()).digest())[:TOPIC_CHARS]

    topic = topic_for(key, _REF)

    assert topic != topic_for(secrets.token_bytes(TOPIC_KEY_BYTES), _REF)
    assert topic != unkeyed
    assert _REF not in topic


async def test_load_or_mint_mints_32_bytes_once_and_keeps_them() -> None:
    store = MemorySecretStore()

    first = await load_or_mint_topic_key(store)
    second = await load_or_mint_topic_key(store)

    assert len(first) == TOPIC_KEY_BYTES
    assert second == first
    assert await store.get(TOPIC_KEY_NAME) == first


async def test_load_or_mint_refuses_a_stored_key_of_the_wrong_length() -> None:
    store = MemorySecretStore()
    await store.put(TOPIC_KEY_NAME, b"short")

    with pytest.raises(PushConfigError, match=r"entrance\.push_topic"):
        await load_or_mint_topic_key(store)
