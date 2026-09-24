# ADR-0034: The Landing Board is versioned by path and pushes only "something is waiting"

- Status: Accepted
- Date: 2026-09-24

## Context

The Landing Board (the Hive Entrance's public API contract, ADR-0032) is used by programs the Hive
does not ship: a home automation hub, another agent framework, a script on a laptop. They are
written from `docs/entrance/openapi.json` alone, so the document is a promise, and changing a
route breaks someone we cannot see. Anything that needs the human (a question, an Alarm that
reached the human, a reply from the Queen, a finished goal, a security event) must reach whichever
device they are on without the device polling, and phones are only reachable through third-party
push services (a browser vendor's Web Push service, or a UnifiedPush distributor on Android) that
the Hive does not control and must not trust with content. Webhooks go out to URLs a device chose,
from inside the operator's network. Two decisions are hard to reverse once clients exist: how the
contract evolves, and what a push carries.

## Decision

**Versioned by path, additive within a version.** Every route lives under `/v1/`. Within `/v1/`
only additive changes are allowed: a new route, a new optional request field, a new response
field, a new member of an enum the document marks open. Anything else (removing or renaming a
route or field, tightening a type, changing a status code's meaning, changing a signing string) is
a breaking change and ships as `/v2/`, with `/v1/` kept for one Brood release beside it.
`entrance/landing_board.py` generates the OpenAPI document from the FastAPI route models; it is
committed as `docs/entrance/openapi.json` and a test fails when the generated document differs
from the committed one, so every route change is a visible contract diff in review. The document
declares the authentication scheme (`securitySchemes`, plus an `x-hive-signing` extension giving
every signed string and encoding of ADR-0033), and each operation carries `x-hive-capability` (the
capability a device must hold) and `x-hive-listeners` (`loopback`, or `loopback` and `remote`); a
loopback-only operation is still described, so a client knows it exists, while the remote
listener answers it with a 404.

**Push carries a notice, never content.** A `PushNotice` has an `event_id` (a ULID, stable across
retries), a `kind` (`question_waiting`, `alarm_waiting`, `reply_waiting`, `goal_completed`,
`security_event`, `withdrawn`), a `ref` (the question, Alarm, goal or security event it points at)
and `created_at`. The device fetches the item itself, over an authenticated session, when the
human looks. On Web Push every payload is padded to the same 512 bytes, so ciphertext length does
not reveal the kind, and the `Topic` header is the first 32 characters of the base64url HMAC-SHA256
of the `ref` under a Hive-local key, so it replaces an undelivered copy without being a stable
identifier anyone else can compute. A push service therefore learns that the Hive wanted a
device's attention, and when; not what for.

**One protocol, four channels.** `entrance/push/base.py` defines `PushChannel` (`deliver(notice,
subscription)`, `withdraw(ref, subscription)`), with:

- `websocket.py` for live clients: an authenticated `/v1/push/stream` socket; delivery is a frame.
- `webhook.py` for programs: an HTTPS POST of the notice JSON to the URL the device registered,
  signed with the Hive's Ed25519 key over `hive-webhook-v1`, the subscription id, the event id, a
  timestamp and the body's SHA-256 (ADR-0033's encodings), in `X-Hive-Signature` with the timestamp
  in `X-Hive-Timestamp`. Every attempt is signed afresh with a new timestamp, so a retry never
  arrives expired; the event id stays the same (receivers dedupe on it); and because the
  subscription id is signed, a notice captured from one receiver cannot be replayed to another.
  Retried with exponential backoff on a network error, a 5xx, 408 or 429; any other 4xx is final.
  The Hive key lives in the secret store, moves with the Hive Stand on Supersedure, and its public
  half is returned at approval so a program pins it.
- `web_push.py` for browsers and phones: RFC 8030 delivery with a `TTL` per kind, RFC 8291
  `aes128gcm` payload encryption and RFC 8292 VAPID, implemented on `cryptography` and `httpx`
  (both already locked), tested against the RFCs' own vectors, so the Hive takes no Web Push
  library. The VAPID key comes from `HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY` when set, otherwise from
  the secret store (minted on first use); its contact from `HIVEMIND_ENTRANCE_VAPID_SUBJECT`. A 404
  or 410 from the push service deletes the subscription.
- The native Android channel (roadmap 12.12) registers through the same Web Push subscription
  shape: UnifiedPush distributors accept RFC 8030 pushes, and the Android ADR in phase 12 decides
  whether FCM is added beside it.
- `fake.py` records deliveries for tests.

**A webhook cannot be aimed inside the Hive.** A registered URL must be `https`, or resolve inside
`[entrance] vpn_cidrs` (the overlay encrypts), or match `[entrance.push] webhook_allowlist`; it may
never resolve to a loopback, link-local or unspecified address or to the Hive Stand's own
addresses. The check runs on the resolved addresses at registration and again at every delivery,
and redirects are not followed, so neither DNS nor a redirect can turn a webhook into a request
against the loopback listener or a metadata endpoint.

**Subscriptions are per device, persisted, re-validated, and filtered by what the device may
read.** A device registers a webhook URL or a Web Push subscription only if it holds
`entrance:push`. Who gets what: `question_waiting` goes to devices holding `entrance:answer`;
`reply_waiting` only to devices that may read the chat (`entrance:submit` and
`honey:clearance:c2`); `goal_completed` only to the device that submitted the goal;
`security_event` and `alarm_waiting` to every other approved device with a subscription
(codingrules 8.15: pushed to every other enrolled device), including "a device is asking to
join". Subscriptions live in the Entrance tables; they are deleted when their device leaves
`APPROVED` (revoked, locked, expired), and on start any whose device is not `APPROVED` is deleted
before the first delivery.

**Answered anywhere, withdrawn everywhere.** When a question is answered or withdrawn, or an Alarm
resolved, the Entrance sends a `withdrawn` notice with the same `ref` to every subscription that
received the original, so the human never answers a question twice.

## Consequences

Positive: a program written from the document keeps working across Hive upgrades within `/v1/`,
signing included; contract changes cannot slip through review; a compromised or curious push
service learns neither content nor kind; a webhook receiver can verify origin, freshness and
destination; one notice shape serves four transports, so adding a channel is a new `PushChannel`,
not a new payload.

Negative: every push costs the device one authenticated fetch before the human sees anything,
which is slower than a push that carries the question. Webhook receivers must verify an Ed25519
signature and dedupe by id, which is more work than a shared secret. Hand-written RFC 8291
encryption is security-sensitive code, which is why it is tested against the RFC's own vectors.
Keeping `/v1/` alive for a Brood beside `/v2/` doubles the routes to test for that window.

## Alternatives considered

Header or media-type versioning: invisible in logs and URLs, and FastAPI's generated document
does not express it cleanly. Date-based versions per request: fine-grained but far more to test.
Pushing the question text: simplest for the human and a leak through every push service it
transits. An HMAC shared secret per webhook: a secret to store and rotate per program, where an
Ed25519 signature needs only the Hive's public key, which clients pin at approval. Signing only
the body: a captured notice could be replayed to another receiver or after its timestamp expired.
`pywebpush`: pulls `requests` and several helpers for a few dozen lines over `cryptography`.
