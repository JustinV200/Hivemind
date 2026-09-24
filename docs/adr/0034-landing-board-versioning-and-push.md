# ADR-0034: The Landing Board is versioned by path and pushes only "something is waiting"

- Status: Proposed
- Date: 2026-09-24

## Context

The Landing Board (the Hive Entrance's public API contract, ADR-0032) is used by programs the Hive
does not ship: a home automation hub, another agent framework, a script on a laptop. They are
written from `docs/entrance/openapi.json` alone, so the document is a promise, and changing a
route breaks someone we cannot see. Anything that needs the human (a question, an Alarm that
reached the human, a reply from the Queen, a finished goal, a security event) must reach whichever
device they are on without the device polling, and phones are only reachable through third-party
push services (a browser vendor's Web Push service, or a UnifiedPush distributor on Android) that
the Hive does not control and must not trust with content. Two decisions are hard to reverse once
clients exist: how the contract evolves, and what a push carries.

## Decision

**Versioned by path, additive within a version.** Every route lives under `/v1/`. Within `/v1/`
only additive changes are allowed: a new route, a new optional request field, a new response
field, a new member of an enum the document marks open. Anything else (removing or renaming a
route or field, tightening a type, changing a status code's meaning) is a breaking change and
ships as `/v2/`, with `/v1/` kept for one Brood release beside it. `entrance/landing_board.py`
generates the OpenAPI document from the FastAPI route models; it is committed as
`docs/entrance/openapi.json` and a test fails when the generated document differs from the
committed one, so every route change is a visible contract diff in review. Each operation carries
two extensions: `x-hive-capability` (the capability a device must hold) and `x-hive-listeners`
(`loopback`, or `loopback` and `remote`); a loopback-only operation is still described, so a
client knows it exists, while the remote listener answers it with a 404 (ADR-0033).

**Push carries a notice, never content.** A `PushNotice` has an `event_id` (a ULID, stable across
retries), a `kind` (`question_waiting`, `alarm_waiting`, `reply_waiting`, `goal_completed`,
`security_event`, `withdrawn`), a `ref` (the question, Alarm, goal or security event it points at)
and `created_at`. The device fetches the item itself, over an authenticated session, when the
human looks. A push service therefore learns only that the Hive wanted this device's attention at
a moment.

**One protocol, four channels.** `entrance/push/base.py` defines `PushChannel` (`deliver(notice,
subscription)`, `withdraw(ref, subscription)`), with:

- `websocket.py` for live clients: an authenticated `/v1/push/stream` socket; delivery is a frame.
- `webhook.py` for programs: an HTTPS POST of the notice JSON to the URL the device registered,
  signed with the Hive's Ed25519 key over `"hive-webhook-v1\n<timestamp>\n<body>"` in
  `X-Hive-Signature` with the timestamp in `X-Hive-Timestamp`, retried with exponential backoff
  on a network error or a 5xx, and idempotent by `event_id` (a retry reuses the id; receivers
  dedupe on it). A 4xx other than 408 and 429 is final.
- `web_push.py` for browsers and phones: RFC 8030 delivery, RFC 8291 `aes128gcm` payload
  encryption and RFC 8292 VAPID, implemented on `cryptography` and `httpx` (both already locked),
  so the Hive takes no Web Push library. The VAPID key comes from
  `HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY` and the contact from `HIVEMIND_ENTRANCE_VAPID_SUBJECT`
  (`manifest/env.py`). The `Topic` header is derived from the notice's `ref`, so a withdrawal
  replaces a copy the push service has not delivered yet. A 404 or 410 from the push service
  deletes the subscription.
- The native Android channel (roadmap 12.12) registers through the same Web Push subscription
  shape: UnifiedPush distributors accept RFC 8030 pushes, and the Android ADR in phase 12 decides
  whether FCM is added beside it.
- `fake.py` records deliveries for tests.

**Subscriptions are per device, capability-filtered, persisted and re-validated.** A device
registers a webhook URL or a Web Push subscription only if it holds `entrance:push`. Who gets
what: `question_waiting` and `alarm_waiting` go to devices holding `entrance:answer`;
`reply_waiting` to devices holding `entrance:submit`; `goal_completed` only to the device that
submitted the goal; `security_event` to every approved device holding `entrance:push`, including
"a device is asking to join". Subscriptions live in the Entrance tables; on start, any whose
device is no longer `APPROVED` is deleted before the first delivery.

**Answered anywhere, withdrawn everywhere.** When a question is answered or withdrawn, or an Alarm
resolved, the Entrance sends a `withdrawn` notice with the same `ref` to every subscription that
received the original, so the human never answers a question twice.

## Consequences

Positive: a program written from the document keeps working across Hive upgrades within `/v1/`;
contract changes cannot slip through review; a compromised or curious push service learns
nothing about the Hive's work; one notice shape serves four transports, so adding a channel is a
new `PushChannel`, not a new payload.

Negative: every push costs the device one authenticated fetch before the human sees anything,
which is slower than a push that carries the question. Webhook receivers must verify an Ed25519
signature and dedupe by id, which is more work than a shared secret. Hand-written RFC 8291
encryption is security-sensitive code; it is tested against the RFC's own test vectors. Keeping
`/v1/` alive for a Brood beside `/v2/` doubles the routes to test for that window.

## Alternatives considered

Header or media-type versioning: invisible in logs and URLs, and FastAPI's generated document
does not express it cleanly. Date-based versions per request: fine-grained but far more to test.
Pushing the question text: simplest for the human and a leak through every push service it
transits. An HMAC shared secret per webhook: a secret to store and rotate per program, where an
Ed25519 signature needs only the Hive's public key, which clients already pin at enrolment.
`pywebpush`: pulls `requests` and several helpers for a few dozen lines over `cryptography`.
