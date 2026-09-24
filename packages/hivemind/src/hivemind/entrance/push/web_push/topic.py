"""Derive the Web Push ``Topic`` for a notice's ref under a Hive-local key, and keep that key.

RFC 8030 section 5.4 lets a push service replace an undelivered message with a newer one that
carries the same ``Topic``; the push channel uses it so a ``withdrawn`` notice replaces the copy of
the original a phone has not yet received. A topic derived from the ref alone (the ref itself, or
its plain hash) would be a stable identifier any push service could correlate across Hives and
match against a guessed id, so it is the first 32 characters (RFC 8030's limit, from the
URL-safe base64 alphabet) of base64url(HMAC-SHA-256(topic key, ref)) under a 32-byte key only this
Hive holds, minted into the secret store as ``entrance.push_topic`` on first use (ADR-0034).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.web_push``.
    ``load_or_mint_topic_key`` is called by the Entrance's composition root; ``topic_for`` by
    ``WebPush`` for every delivery. Calls into ``hmac``, the ``SecretStore`` it is given and
    ``hivemind.entrance.auth`` (base64url).

Key invariants:
    - The same key and ref always give the same topic; ``TOPIC_CHARS`` characters of
      ``[A-Za-z0-9_-]``.
    - The key leaves this module only to the secret store; it is never logged.

See Also:
    - RFC 8030 section 5.4 for the header.
    - docs/adr/0034-landing-board-versioning-and-push.md for why the topic is keyed.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from hivemind.common.secrets import SecretStore
from hivemind.entrance.auth import b64url_encode
from hivemind.entrance.push.errors import PushConfigError

TOPIC_KEY_NAME = "entrance.push_topic"  # The secret store name of the Topic key.
TOPIC_KEY_BYTES = 32  # One SHA-256 block of key: HMAC gains nothing from more.
TOPIC_CHARS = 32  # RFC 8030 section 5.4: a Topic is at most 32 base64url characters.

__all__ = [
    "TOPIC_CHARS",
    "TOPIC_KEY_BYTES",
    "TOPIC_KEY_NAME",
    "load_or_mint_topic_key",
    "topic_for",
]


def topic_for(topic_key: bytes, ref: str) -> str:
    """Return the ``Topic`` header value for every notice about ``ref``.

    Args:
        topic_key: The Hive's Topic key.
        ref: The notice's ref.

    Returns:
        The first ``TOPIC_CHARS`` characters of base64url(HMAC-SHA-256(topic_key, ref)).
    """
    digest = hmac.new(topic_key, ref.encode("utf-8"), hashlib.sha256).digest()
    return b64url_encode(digest)[:TOPIC_CHARS]


async def load_or_mint_topic_key(store: SecretStore) -> bytes:
    """Return the Hive's Topic key, minting and storing it on first use.

    Args:
        store: The Hive's secret store.

    Returns:
        The ``TOPIC_KEY_BYTES``-byte key; the same one on every call once it exists.

    Raises:
        PushConfigError: The stored value is not ``TOPIC_KEY_BYTES`` bytes long.
    """
    # Latency: one small file read (a dict lookup in tests).
    stored = await store.get(TOPIC_KEY_NAME)
    if stored is not None:
        # A truncated key would still hash; refusing it keeps topics stable across restarts.
        if len(stored) != TOPIC_KEY_BYTES:
            raise PushConfigError(TOPIC_KEY_NAME, f"{TOPIC_KEY_BYTES} random bytes")
        return stored
    key = secrets.token_bytes(TOPIC_KEY_BYTES)
    await store.put(TOPIC_KEY_NAME, key)
    return key
