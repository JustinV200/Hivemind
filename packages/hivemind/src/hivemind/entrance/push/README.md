# hivemind.entrance.push

The push channel tells an enrolled device that **something is waiting** for the human (a
question, an Alarm that reached the human, a reply from the Queen, a finished goal, an Entrance
security event) without the device polling, and **never what** (ADR-0034). A push carries a
`PushNotice`: an event id (a ULID minted once, reused by every retry and every channel), a kind, the
id of the item it points at (`ref`) and a time. The device fetches the item itself, over its
authenticated session, when the human looks.

## Layout

| Module | What it holds |
|---|---|
| `models.py` | `PushNotice`, `NoticeKind`, `notice_json` (the one byte form every channel sends), `Subscription`, `WebPushKeys`, `ChannelKind`, `DeliveryOutcome`. |
| `base.py` | `PushChannel`: `deliver(notice, subscription) -> DeliveryOutcome`. Withdrawal is a `withdrawn` notice through the same method. |
| `webhook.py` | `WebhookPush`: POSTs the notice JSON signed with the Hive's Ed25519 key, re-signed with a fresh timestamp on every attempt, retried with backoff on a network error, 5xx, 408 or 429. |
| `web_push/` | `WebPush` (RFC 8030) with `ece` (RFC 8291 `aes128gcm`, plus the user agent's `decrypt`), `vapid` (RFC 8292) and `topic` (the keyed `Topic`), on `cryptography` and `httpx` only. |
| `websocket.py` | `LivePush`: the in-memory hub of live push sockets per device. |
| `fake.py` | `FakePush`: records deliveries and answers scripted outcomes. |
| `destinations.py` | `DestinationGuard`: the SSRF guard every registration and every delivery goes through. |
| `audience.py` | `audience_for`: the pure rule for who hears which kind. |
| `registration.py` | `Admission`: who may register a subscription. |
| `store/` | `SubscriptionStore`, SQLite (migration series `entrance_push`) and in-memory: subscriptions and the per-ref delivery log. |
| `dispatch/` | `PushDispatcher` (register, push, withdraw, forget a device, re-validate), its `Courier` and per-ref bookkeeping. |

## How a notice travels

```
raise a notice ──> audience_for(kind, devices, concerning=, submitter=)  (pure)
                          │
                          v
PushDispatcher.push(notice, audience)      [holds the ref's lock]
   ├── live sockets of every audience device ──> LivePush (one text frame each)
   └── every stored subscription of them ──────> WebhookPush / WebPush
            each: DestinationGuard.vet (resolve, check, pin) ─> POST to the checked address
   settle: GONE -> delete the subscription; DELIVERED -> record ref -> subscription

PushDispatcher.withdraw(ref)               [waits for an in-flight push of ref]
   └── a `withdrawn` notice to exactly the recorded subscriptions and live devices, then forget ref
```

## Who hears what

Only APPROVED devices holding `entrance:push`, and then: `question_waiting` to devices holding
`entrance:answer`; `reply_waiting` to devices holding `entrance:submit` and `honey:clearance:c2`;
`goal_completed` to the submitting device only; `security_event` and `alarm_waiting` to every
device except the one the event concerns; `withdrawn` to the original's recipients only.

## What a push service or receiver can learn

- **Web Push**: every plaintext is padded to exactly 512 bytes, so every body is 614 bytes; `TTL`
  and `Urgency` are the same for every "waiting" kind (only `withdrawn` differs, and it reuses the
  original's `Topic` anyway); the `Topic` is the first 32 characters of
  base64url(HMAC-SHA-256(key, ref)) under the `entrance.push_topic` secret.
- **Webhooks**: `X-Hive-Signature` is base64url(Ed25519) by the Hive key (`hive.ed25519`) over
  `webhook_string(subscription id, event id, X-Hive-Timestamp, sha256(body))`; the receiver verifies
  it with the Hive's public key, checks the timestamp, and dedupes on `X-Hive-Event-Id`.

## Destinations never point inside the Hive

A destination must be `https`, or resolve entirely inside `[entrance] vpn_cidrs`, or match
`[entrance.push] webhook_allowlist` (hosts, or networks every resolved address is in). It is always
refused when any resolved address is loopback, link-local, unspecified, multicast, an IPv4-mapped
form of those, or one of the Hive Stand's own addresses; when it carries credentials; or when its
name does not resolve. The guard runs at registration and again before every delivery, and the
request is **pinned** to the address that was checked (the name goes in `Host` and TLS SNI, and
the connection is not pooled), so a DNS answer that changes between the check and the connection
cannot redirect it. Redirects are never followed. The guard is applied to Web Push endpoints too.

The composition root should build the `httpx.AsyncClient` it injects with `trust_env=False`, so an
environment proxy never stands between the pinned address and the connection.

## Keys

- VAPID: `HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY` (base64url raw P-256 scalar) when set, else the
  secret store's `entrance.vapid`, minted on first use (`load_or_mint_vapid_key`); its contact is
  `HIVEMIND_ENTRANCE_VAPID_SUBJECT` (a `mailto:` or `https:` URI). `VapidSigner.application_server_key`
  is what the subscribe route hands a browser.
- Topic: the secret store's `entrance.push_topic`, minted on first use (`load_or_mint_topic_key`).
- Webhook signing: the Hive's own key, `load_or_mint_hive_signer`.

No key, auth secret, endpoint or payload is ever logged; errors name a refusal's reason, never a URL.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/push \
    packages/hivemind/tests/contracts/test_push_subscription_store_contract.py
```

No network is needed: HTTP goes through `httpx.MockTransport`, name resolution through a static
resolver, and backoff sleeps through a stepping `FakeClock`. `web_push/test_ece.py` reproduces
RFC 8291 Appendix A byte for byte and decrypts it back; `dispatch/test_end_to_end.py` runs the real
channels, the real SQLite store and the real crypto together: a question pushed to a phone (Web
Push, decrypted with the phone's own key) and a program (a signed webhook, verified with the Hive's
public key), answered, and withdrawn from both.
