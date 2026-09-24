"""Deliver push notices through browsers' and phones' push services: RFC 8030, 8291 and 8292.

Web Push reaches a device that has no socket open: its browser, or its phone's push distributor,
holds a subscription at a push service the Hive does not control and must not trust with content
(ADR-0034). This package is that channel, built on ``cryptography`` and ``httpx`` alone, with no
Web Push library: ``ece`` encrypts each payload to the user agent's key (RFC 8291 ``aes128gcm``)
and decrypts one as a user agent would; ``vapid`` holds the Hive's VAPID key and signs the RFC 8292
header; ``topic`` derives the keyed ``Topic`` that lets a withdrawal replace an undelivered
notice; ``channel`` is ``WebPush``, the ``PushChannel`` that puts them together with a notice
padded to one fixed length.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Built by the
    Entrance's composition root (the key loaders, then ``WebPush``); called by
    ``hivemind.entrance.push.dispatch.PushDispatcher``. Calls into the push records, the
    destination guard, the secret store and ``cryptography`` and ``httpx``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No key, auth secret, endpoint or payload is ever logged or raised in a message.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the decisions.
    - RFC 8030, RFC 8188, RFC 8291 and RFC 8292.

Public API:
    - WebPush, web_push_outcome, NOTICE_PADDED_BYTES, TTL_BY_KIND, URGENCY_BY_KIND,
      WEB_PUSH_TIMEOUT_S: the channel.
    - encrypt, decrypt, ReceiverKeys, SenderMaterial, HEADER_BYTES, RECORD_SIZE, TAG_BYTES: RFC 8291
      payload encryption, and the user agent's decryption.
    - VapidKey, VapidSigner, load_or_mint_vapid_key, vapid_audience, VAPID_KEY_NAME,
      VAPID_JWT_TTL_S: RFC 8292 VAPID.
    - topic_for, load_or_mint_topic_key, TOPIC_KEY_NAME, TOPIC_KEY_BYTES, TOPIC_CHARS: the
      keyed Topic.
"""

from hivemind.entrance.push.web_push.channel import (
    NOTICE_PADDED_BYTES,
    TTL_BY_KIND,
    URGENCY_BY_KIND,
    WEB_PUSH_TIMEOUT_S,
    WebPush,
    web_push_outcome,
)
from hivemind.entrance.push.web_push.ece import (
    HEADER_BYTES,
    RECORD_SIZE,
    TAG_BYTES,
    ReceiverKeys,
    SenderMaterial,
    decrypt,
    encrypt,
)
from hivemind.entrance.push.web_push.topic import (
    TOPIC_CHARS,
    TOPIC_KEY_BYTES,
    TOPIC_KEY_NAME,
    load_or_mint_topic_key,
    topic_for,
)
from hivemind.entrance.push.web_push.vapid import (
    VAPID_JWT_TTL_S,
    VAPID_KEY_NAME,
    VapidKey,
    VapidSigner,
    load_or_mint_vapid_key,
    vapid_audience,
)

__all__ = [
    "HEADER_BYTES",
    "NOTICE_PADDED_BYTES",
    "RECORD_SIZE",
    "TAG_BYTES",
    "TOPIC_CHARS",
    "TOPIC_KEY_BYTES",
    "TOPIC_KEY_NAME",
    "TTL_BY_KIND",
    "URGENCY_BY_KIND",
    "VAPID_JWT_TTL_S",
    "VAPID_KEY_NAME",
    "WEB_PUSH_TIMEOUT_S",
    "ReceiverKeys",
    "SenderMaterial",
    "VapidKey",
    "VapidSigner",
    "WebPush",
    "decrypt",
    "encrypt",
    "load_or_mint_topic_key",
    "load_or_mint_vapid_key",
    "topic_for",
    "vapid_audience",
    "web_push_outcome",
]
