"""Tell enrolled devices that something is waiting, over live sockets, webhooks and Web Push.

Anything that needs the human (a question, an Alarm that reached the human, a reply from the
Queen, a finished goal, an Entrance security event) must reach whichever device they are on
without the device polling (ADR-0042). A push carries a notice, never content: an event id, a
kind, the id of the item and a time; the device fetches the item itself over its authenticated
session when the human looks. ``models`` defines the notice and the subscription; ``base`` the
``PushChannel`` protocol; ``webhook`` (programs, signed with the Hive's key), ``web_push``
(browsers and phones, RFC 8030/8291/8292 on ``cryptography`` and ``httpx``) and ``fake`` its
implementations; ``websocket`` the live socket hub; ``destinations`` the guard that keeps every
destination from reaching inside the Hive; ``audience`` who hears what; ``registration`` who may
subscribe; ``store`` the persisted subscriptions and delivery log; ``dispatch`` the entry point
that pushes and withdraws.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the entrance package. Built by the
    Entrance's composition root; called by the subscribe and push-stream routes, by whatever
    raises a notice, and by the device state changes. Calls into ``hivemind.entrance.auth``,
    ``hivemind.entrance.enrol``, ``hivemind.guard``, ``hivemind.manifest.schema``,
    ``hivemind.common`` and waggle.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A push never carries content, and every Web Push ciphertext has the same length.
    - No destination ever resolves to loopback, link-local, unspecified or multicast addresses
      or to the Hive Stand's own, checked at registration and again before every delivery.
    - Nothing here logs a key, an auth secret, an endpoint or a payload.

See Also:
    - docs/adr/0042-landing-board-versioning-and-push.md for the decisions.
    - .claude/codingrules.md section 8.15 ("Push, not polling") and section 8.1 (PushChannel).

Public API:
    - PushNotice, NoticeKind, notice_json, Subscription, SubscriptionId, new_subscription_id,
      ChannelKind, WebPushKeys, DeliveryOutcome: the records.
    - PushChannel: the protocol; WebhookPush, WebPush, FakePush: its implementations.
    - LivePush, LiveAttachment, LiveSender: the live WebSocket hub.
    - DestinationGuard, DestinationPolicy, DestinationRefusal, VettedDestination, Resolver,
      system_resolver: the destination guard.
    - Audience, audience_for: who hears which kind of notice.
    - Admission, registration_refusal: who may subscribe.
    - SubscriptionStore, SqliteSubscriptionStore, MemorySubscriptionStore,
      apply_push_migrations: the persisted subscriptions and delivery log.
    - PushDispatcher, PushChannels, PushReport: the entry point.
    - VapidKey, VapidSigner, load_or_mint_vapid_key, load_or_mint_topic_key, decrypt: the Web
      Push keys, and the user agent's decryption (the rest via hivemind.entrance.push.web_push).
    - PushError and its subclasses: every refusal the push channel makes on purpose.
"""

from hivemind.entrance.push.audience import Audience, audience_for
from hivemind.entrance.push.base import PushChannel
from hivemind.entrance.push.destinations import (
    DestinationGuard,
    DestinationPolicy,
    DestinationRefusal,
    Resolver,
    VettedDestination,
    system_resolver,
)
from hivemind.entrance.push.dispatch import PushChannels, PushDispatcher, PushReport
from hivemind.entrance.push.errors import (
    DestinationRefusedError,
    LiveSocketClosedError,
    PushConfigError,
    PushError,
    PushRegistrationRefusedError,
    SubscriptionExistsError,
    SubscriptionNotFoundError,
)
from hivemind.entrance.push.fake import FakePush
from hivemind.entrance.push.models import (
    ChannelKind,
    DeliveryOutcome,
    NoticeKind,
    PushNotice,
    Subscription,
    SubscriptionId,
    WebPushKeys,
    new_subscription_id,
    notice_json,
)
from hivemind.entrance.push.registration import Admission, registration_refusal
from hivemind.entrance.push.store import (
    MemorySubscriptionStore,
    SqliteSubscriptionStore,
    SubscriptionStore,
    apply_push_migrations,
)
from hivemind.entrance.push.web_push import (
    VapidKey,
    VapidSigner,
    WebPush,
    decrypt,
    load_or_mint_topic_key,
    load_or_mint_vapid_key,
)
from hivemind.entrance.push.webhook import WebhookPush
from hivemind.entrance.push.websocket import LiveAttachment, LivePush, LiveSender

__all__ = [
    "Admission",
    "Audience",
    "ChannelKind",
    "DeliveryOutcome",
    "DestinationGuard",
    "DestinationPolicy",
    "DestinationRefusal",
    "DestinationRefusedError",
    "FakePush",
    "LiveAttachment",
    "LivePush",
    "LiveSender",
    "LiveSocketClosedError",
    "MemorySubscriptionStore",
    "NoticeKind",
    "PushChannel",
    "PushChannels",
    "PushConfigError",
    "PushDispatcher",
    "PushError",
    "PushNotice",
    "PushRegistrationRefusedError",
    "PushReport",
    "Resolver",
    "SqliteSubscriptionStore",
    "Subscription",
    "SubscriptionExistsError",
    "SubscriptionId",
    "SubscriptionNotFoundError",
    "SubscriptionStore",
    "VapidKey",
    "VapidSigner",
    "VettedDestination",
    "WebPush",
    "WebPushKeys",
    "WebhookPush",
    "apply_push_migrations",
    "audience_for",
    "decrypt",
    "load_or_mint_topic_key",
    "load_or_mint_vapid_key",
    "new_subscription_id",
    "notice_json",
    "registration_refusal",
    "system_resolver",
]
