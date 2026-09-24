# The Landing Board: a client guide

The Landing Board is the public contract of the Hive Entrance, the one door into a HiveMind Hive.
This guide is for someone writing a client (a program, a script, a home-automation hub, another
agent framework) who has the contract, [`openapi.json`](openapi.json), and nothing else. It walks
through the calls a client needs, in the order it needs them, with `curl` examples you can run.

You need three things:

- **The document.** [`openapi.json`](openapi.json) in this directory. Each listener also serves the
  exact same bytes, unauthenticated, at `GET /v1/openapi.json`. That route is deliberately not
  listed inside the document.
- **This guide.** Everything here matches the document. When the guide states something the
  document does not yet say, it is marked **Not in the document**. The Entrance does behave that
  way today, but the versioning promise (section 11) does not cover it yet.
- **For the curl examples:** [`examples/hive-sign.sh`](examples/hive-sign.sh), a POSIX shell script
  that signs what the Entrance asks a device to sign. You also need OpenSSL 3, `curl` 7.76 or later
  and `jq`.

The Hive's own conformance test (`packages/hivemind/tests/e2e/test_landing_board_conformance.py`)
drives a real Entrance through a client that reads only the document. Another test
(`test_landing_board_guide.py`) runs every `curl` example below, in order, against a live Entrance.

## Contents

1. [What the Landing Board is](#1-what-the-landing-board-is)
2. [Never forward TCP into the loopback listener](#2-never-forward-tcp-into-the-loopback-listener)
3. [Reading the document](#3-reading-the-document)
4. [Enrolling a device](#4-enrolling-a-device)
5. [Logging in](#5-logging-in)
6. [Signing every request](#6-signing-every-request)
7. [Step-up and held requests](#7-step-up-and-held-requests)
8. [The three calls: submit, subscribe, answer](#8-the-three-calls-submit-subscribe-answer)
9. [The push contract](#9-the-push-contract)
10. [Errors and refusals](#10-errors-and-refusals)
11. [Versioning](#11-versioning)

## 1. What the Landing Board is

The Hive Entrance runs inside the Hive's own process on the **Hive Stand**, the machine the Queen
(the Hive's orchestrator) runs on. It serves the Landing Board on up to two listeners:

- **The loopback listener** is always on. It listens at `[entrance] bind`, which defaults to
  `127.0.0.1:8710`, and only software on the Hive Stand can reach it. It answers only requests whose
  `Host` names a loopback host on its own port. It refuses any request that carries a `Forwarded`,
  `Via`, `X-Real-IP`, `X-Forwarded-*` or `Tailscale-*` header with a bare `403`, which has no body.
- **The remote listener** exists only when the operator exposes the Entrance (`[entrance] expose`).
  The mode is `vpn` (recommended: an address on a Tailscale or WireGuard overlay), `lan` or `tunnel`.
  Every remote mode speaks TLS on a DNS name. `lan` and `tunnel` also require mutual TLS with a
  client certificate from the Hive's own certificate authority, issued when the device is approved.
  There is no public mode.

Every operation in the document names its listeners in `x-hive-listeners`, as `["loopback"]` or
`["loopback", "remote"]`. A loopback-only operation is still described, so a client knows it
exists. On the remote listener it was never mounted, so it answers `404` (see section 10), never
`403`. The routes that admit or empower a device are loopback-only. That covers minting invites,
approving, denying, unlocking, changing a device's capabilities, revoking and reopening a reduced
Entrance. A remote session, however it was obtained, cannot reach any of them.

A client is always a **device**: it holds its own key, it is enrolled with an invite and approved
by the operator at the Hive Stand, and it logs in with its key plus the operator's password. There
is no sign-up and no shared API key.

## 2. Never forward TCP into the loopback listener

> **Warning: nothing may forward TCP into the loopback listener.** That rules out `socat`, `ssh -L`
> or `ssh -R` onto its port, a reverse proxy, `tailscale serve`, and any tunnel pointed at it. The
> loopback listener trusts that its peer is a process on the Hive Stand. Forwarding into it would
> put the approval routes (minting invites, approving and unlocking devices, widening
> capabilities, reopening the door) on the network. Its `Host` check and its refusal of forwarding
> headers stop the common proxies, but not a raw TCP forwarder. The rule is yours to keep.
>
> Remote access goes only through the remote listener that `[entrance] expose` opens: `vpn`, `lan`
> (with TLS and mutual TLS) or `tunnel`. In `tunnel` mode the Entrance runs the tunnel client
> itself, pointed at the remote listener with TLS kept end to end, never at the loopback one.

For the same reason, every example below passes `--noproxy '*'` to `curl`: a request to the
Entrance must never pass through a proxy.

## 3. Reading the document

The document is OpenAPI 3.1. `info.version` is `v1` and every path starts with `/v1/`. Beyond the
standard fields, it carries these extensions:

| Extension | Where | Meaning |
|---|---|---|
| `x-hive-listeners` | each operation, each stream | Which listeners serve it: `loopback`, or `loopback` and `remote`. |
| `x-hive-capability` | each operation, each stream | The capability the device must hold. Absent on an operation means any approved device's own session (or none, for a public one). |
| `x-hive-c2` | each operation, each stream | `true`: the answer holds personal (`C2`) content, so the device must also hold `honey:clearance:c2`. |
| `x-hive-effect` | each operation | What it changes: `read`, `session`, `inbox` (a write into the Queen's inbox), `push`, or `door` (the Entrance itself). |
| `x-hive-switch` | some operations | Mounted only while that `[entrance]` switch is on; otherwise a `404`. Today only the steward approval route has one (`steward_devices`). |
| `x-hive-signing` | top level | Every signed string, its fields, its encodings, the header names, and a worked example. |
| `x-hive-streams` | top level | The WebSocket views: path, first frame, frame schema, capability and listeners. |

An operation whose `security` is an empty list is public: enrolment and the login ceremony. Every
other operation names `HiveSession`, a bearer token that is refused unless the request is also
signed (section 6).

The examples below use four shell variables. Set them before you run anything:

```sh
HIVE_URL=http://127.0.0.1:8710                   # or your remote listener, e.g. https://hive.example.ts.net
HIVE_SIGN=/path/to/docs/entrance/examples/hive-sign.sh
INVITE_CODE='ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ'   # the code the operator gave you
export HIVE_PASSWORD                             # the operator's password, from your secret store
```

The examples keep their state in files in the current directory (`device.pem`, `hive_id`,
`device_id`, `token` and so on), and they are written to run under `sh -eu`.

## 4. Enrolling a device

Enrolment is invite, key, pending, approval. Only the approval happens at the Hive Stand.

**1. Receive an invite.** The operator mints one on the Hive Stand, with `hive entrance invite` or
the loopback-only `POST /v1/entrance/invites`, and hands you the code. The code is 26 base32
characters in dash-separated groups of four, for example `ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ`. It is
also a link, `<base>/enrol#code=<code>`, shown as a QR code. An invite is single use and expires
after `[entrance] invite_ttl_minutes`, 15 minutes by default.

**2. Read the Hive's id.** `GET /v1/enrol/hive` is public and answers `HiveView`, whose `hive_id`
is a `hive_...` id. The enrolment and login signatures both name it, so a signature made for one
Hive is refused by every other.

**3. Mint an Ed25519 key.** Keep the private key where your platform keeps secrets. The Entrance
only ever sees the public key. A browser uses a passkey instead:
`POST /v1/enrol/passkey-options`, then `navigator.credentials.create`, then `POST /v1/enrol/passkey`.
The rest of this guide covers programs with Ed25519 keys.

**4. Sign and redeem.** Sign `hive-enrol-v1`, whose fields are the Hive id, the SHA-256 of the
invite code in lowercase hex, and your raw 32-byte public key in lowercase hex. Then send
`Ed25519Redemption` to `POST /v1/enrol/ed25519`: the `code`, the `public_key_hex`, the `signature`
(unpadded base64url) and a `description` (`name`, `platform`, `user_agent`: display text for the
operator). The answer is `202` with a `RedemptionView`:

- `device_id`: your device, now `PENDING`. Keep it: you log in with it.
- `fingerprint`: your key's fingerprint. Read it to the operator, who sees the same one at approval.
- `hive_public_key_hex`: the Hive's own Ed25519 key. Pin it, because it signs every webhook
  (section 8.2).

The code is hashed in the form the Hive Stand shows it, upper case with the dashes
(`ABCD-EFGH-...-YZ`), as UTF-8; the document's `x-hive-signing.invite_code` states the form and
carries a worked example (a code as typed, its canonical form and its SHA-256). The helper script
puts a code typed in any case, with or without dashes, into that form before hashing it. The `code`
member of the body may be sent as typed.

<!-- run: enrol -->
```sh
umask 077                                   # the key and the session files are for this user only
openssl genpkey -algorithm ed25519 -out device.pem
curl -sS --fail-with-body --noproxy '*' "$HIVE_URL/v1/enrol/hive" > hive.json
jq -r .hive_id hive.json > hive_id
jq -n --arg code "$INVITE_CODE" \
      --arg key "$("$HIVE_SIGN" public-key device.pem)" \
      --arg signature "$("$HIVE_SIGN" enrol device.pem "$(cat hive_id)" "$INVITE_CODE")" \
      '{code: $code, public_key_hex: $key, signature: $signature,
        description: {name: "garden-bot", platform: "Linux", user_agent: "curl"}}' > redeem.json
curl -sS --fail-with-body --noproxy '*' -X POST "$HIVE_URL/v1/enrol/ed25519" \
     -H 'Content-Type: application/json' --data-binary @redeem.json > redeemed.json
jq -r .device_id redeemed.json > device_id
jq -r .hive_public_key_hex redeemed.json > hive_key
jq -r .fingerprint redeemed.json            # read this to the operator
```

A second redemption of the same code, like an unknown, expired or malformed code or a bad
signature, is `403` with `hivemind.entrance.enrolment_refused`. All of those refusals read alike on
purpose. An unauthenticated device learns nothing it could use to probe invites.

**5. Wait for the operator.** The operator approves the device at the Hive Stand, with
`hive entrance approve` or the loopback-only `POST /v1/entrance/pending/{device_id}/approve`. The
approval binds its name, its capabilities (never wider than the device role's ceiling), a daily
spend cap, an expiry, and whether a person types at it (*interactive*, section 7). Until then the
device cannot log in. Asking for a login challenge answers `401` with
`hivemind.entrance.authentication_failed`:

<!-- run: enrol -->
```sh
curl -sS --noproxy '*' -o /dev/null -w '%{http_code}\n' -X POST "$HIVE_URL/v1/auth/challenge" \
     -H 'Content-Type: application/json' -d "{\"device_id\":\"$(cat device_id)\"}"
```

The answer is the same `401` whether the device is pending, denied, expired or unknown. The
Entrance never says which. An unapproved request expires after `[entrance] pending_ttl_hours`,
24 hours by default. An unauthenticated device has no push channel, so either ask the operator or
retry the challenge now and then. Challenges are unauthenticated, so they count against
`[entrance] rate_limit_per_address`, 30 requests a minute by default.

## 5. Logging in

Login is two factors, the device key and the operator's password. The key proof is checked first.

1. `POST /v1/auth/challenge` with `ChallengeRequest` (`device_id`) answers `200` with a
   `ChallengeView`: a single-use `nonce` that expires at `expires_at`, 60 seconds later.
   `passkey_options` is `null` for an Ed25519 device.
2. Sign `hive-login-v1`, whose fields are the Hive id, your device id and the challenge `nonce`.
3. `POST /v1/auth/login` with `LoginRequest` (`device_id`, `nonce`, `signature`, `password`)
   answers `201` with an `OpenedSessionView`:
   - `token`: the session's bearer token, handed out once. The Entrance keeps only its hash.
   - `listener`: `loopback` or `remote`. The session works only on that listener.
   - `expires_at`: its absolute end, after at most `session_ttl_hours` (12 by default). It also
     dies after `idle_timeout_minutes` (30 by default) without use, at logout, and when the device
     is locked, revoked or expires.
   - `needs_step_up`: the travel lock saw the device on a new network, so step up (section 7)
     before anything else.

A failed login is `401` and never says which factor failed. A bad device proof is never charged to
the device, because device ids are not secret. A valid proof with a wrong password is: after
`[entrance] lockout_attempts` of them in a row (5 by default), the device is `LOCKED` until the
operator unlocks it on loopback. A challenge is spent by its first answer, right or wrong.

<!-- run: login -->
```sh
curl -sS --fail-with-body --noproxy '*' -X POST "$HIVE_URL/v1/auth/challenge" \
     -H 'Content-Type: application/json' -d "{\"device_id\":\"$(cat device_id)\"}" > challenge.json
nonce=$(jq -r .nonce challenge.json)
signature=$("$HIVE_SIGN" login device.pem "$(cat hive_id)" "$(cat device_id)" "$nonce")
# The password comes from the environment, so it never sits on a command line.
jq -n --arg device_id "$(cat device_id)" --arg nonce "$nonce" --arg signature "$signature" \
      '{device_id: $device_id, nonce: $nonce, signature: $signature, password: env.HIVE_PASSWORD}' |
  curl -sS --fail-with-body --noproxy '*' -X POST "$HIVE_URL/v1/auth/login" \
       -H 'Content-Type: application/json' --data-binary @- > session.json
jq -r .token session.json > token
printf 'Authorization: Bearer %s\n' "$(cat token)" > auth.header
jq '{device_id, listener, expires_at, needs_step_up}' session.json
rm session.json
```

A program's Ed25519 device key also signs its session's requests: it is the session's *binding
key*. A browser instead registers a WebCrypto ECDSA P-256 key as `binding_key`, in the challenge
request and again in the login request, and signs with that. Its signatures are 64 bytes in IEEE
P1363 form (`r || s`).

## 6. Signing every request

A token alone is refused. Every request to an operation that names `HiveSession` carries four
headers. `x-hive-signing.headers` gives their names:

| Header | Value |
|---|---|
| `Authorization` | `Bearer <token>` |
| `X-Hive-Timestamp` | now, in integer Unix seconds |
| `X-Hive-Nonce` | at least 16 fresh random bytes, unpadded base64url; never reused |
| `X-Hive-Signature` | the binding key's signature over `hive-request-v1`, unpadded base64url |

`hive-request-v1` has five fields, in this order:

1. the method, upper case;
2. the raw path and query exactly as sent (`/v1/chat?limit=20`, or the path alone), with
   percent-encoding untouched;
3. the `X-Hive-Timestamp` value;
4. the `X-Hive-Nonce` value;
5. the request body's SHA-256 in lowercase hex. With no body, that is the hash of the empty
   string, `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

Each signed string (`x-hive-signing.encoding`) is its tag, then its fields, one per line, joined
by a single line feed (U+000A), with no trailing newline, encoded as UTF-8. No field may contain a
line break. `x-hive-signing.example` is a worked example. Rebuild its `signed_string` from its
fields before you sign anything real.

The Entrance refuses a request with `401` (`hivemind.entrance.authentication_failed`, without
saying why) when:

- the token is unknown, ended, expired or idle;
- the signature does not verify under the session's binding key;
- the timestamp is more than `[entrance] request_skew_s` from the Hive Stand's clock (60 seconds
  by default), so keep your clock synchronised;
- the nonce was already used (nonces are remembered, across restarts, for twice the skew window);
- the session was opened on the other listener;
- the device is no longer `APPROVED`.

A `401` means: log in again. Sign the body bytes you actually send. `curl --data-binary @file` sends
a file's bytes unchanged, and the helper hashes the same file:

```sh
"$HIVE_SIGN" request device.pem GET /v1/devices/me > signed.headers   # prints the three X-Hive-* headers
```

`POST /v1/auth/logout` ends the session (`204`, no body) and closes its sockets:

<!-- run: logout -->
```sh
"$HIVE_SIGN" request device.pem POST /v1/auth/logout > signed.headers
curl -sS --fail-with-body --noproxy '*' -X POST "$HIVE_URL/v1/auth/logout" \
     -H @auth.header -H @signed.headers -o /dev/null -w '%{http_code}\n'
rm -f token auth.header
```

## 7. Step-up and held requests

Some requests need a *step-up*: both factors again, fresh, within the last
`[entrance] step_up_window_minutes` (5 by default). ADR-0033 lists them. They include a goal whose
`budget_usd` is above `[entrance] step_up_spend`, a goal that would take the device past its daily
spend cap, capability and key changes, reopening a reduced Entrance, locking another device, and
the break-glass actions. Without a step-up the answer is `403`, with `ErrorBody.error`
`hivemind.entrance.step_up_required` and a `reason`: one of `over_step_up_spend`, `over_daily_cap`,
`sensitive_action` or `new_network`.

- **An interactive device** (a person types the password at it) steps up and asks again.
  `POST /v1/auth/step-up/challenge` answers a `ChallengeView`. Sign `hive-login-v1` over its
  `nonce`, then `POST /v1/auth/step-up` with `StepUpRequest` (`nonce`, `signature`, `password`)
  answers `200` with `SteppedUpView` (`stepped_up_until`).
- **A device no person types at** (a program, unless the operator approved it as interactive)
  cannot step up. Its request is *held* instead. The `403` carries a `pending_id` (`pend_...`), and
  the human is told on their other devices. `GET /v1/entrance/confirmations` lists what is held.
  From an interactive device, inside its step-up window, a person confirms with
  `POST /v1/entrance/confirmations/{pending_id}/confirm`, which answers `ConfirmedView` (for a goal,
  its `goal_request_id`), or declines with `.../cancel`. A held goal is submitted exactly once,
  under the id it was given when it was held. A program is never let past its spend cap by its own
  two factors.

## 8. The three calls: submit, subscribe, answer

### 8.1 Submit a goal

`POST /v1/goals` takes a `GoalSubmission`:

- `text` (required);
- `budget_usd`, which can only lower the Hive's per-goal cap;
- `clearance`, the data-sensitivity ceiling (`C1` by default);
- `comb_shield`, a security tier. `NIGHT_VEIL` needs `cell:comb_shield:night_veil`.

The answer is `202` with a `GoalAccepted` (`id`, a `goalreq_...` id, and `state` `RECEIVED`), and
only once the request is committed in the Queen's own tables. A `202` is durable: a crash
afterwards loses nothing, and the Queen plans the goal on her own tick. The operation needs
`entrance:submit`. Step-up rules apply (section 7).

<!-- run: submit -->
```sh
printf '%s' '{"text":"write three haiku about bees to separate files"}' > goal.json
"$HIVE_SIGN" request device.pem POST /v1/goals goal.json > signed.headers
curl -sS --fail-with-body --noproxy '*' -X POST "$HIVE_URL/v1/goals" \
     -H @auth.header -H @signed.headers -H 'Content-Type: application/json' \
     --data-binary @goal.json > accepted.json
jq -r .id accepted.json > goal_request
jq -c . accepted.json
```

Follow it with `GET /v1/goals/{request_id}`, which answers a `GoalView` without the goal's text. A
device reads only the requests it submitted. `state` moves from `RECEIVED` through `PLANNING` to
`PLANNED`, at which point `goal_id` is set. It can instead become `REFUSED` (`refused` is `true`,
and the reason is in the chat), or `AWAITING_CONFIRMATION` (`needs_confirmation` is `true`; answer
with `.../confirm` or `.../decline`). The goal is done when `finished_at` is set. Rather than polling
for that, subscribe (8.2): a `goal_completed` notice whose `ref` is the goal request's id arrives
when every task of the goal has finished.

<!-- run: follow -->
```sh
target="/v1/goals/$(cat goal_request)"
"$HIVE_SIGN" request device.pem GET "$target" > signed.headers
curl -sS --fail-with-body --noproxy '*' "$HIVE_URL$target" -H @auth.header -H @signed.headers |
  jq -c '{state, goal_id, finished_at}'
```

### 8.2 Subscribe

Clients do not poll. The Entrance pushes a content-free notice whenever something waits for the
human (section 9), over three channels.

**WebSocket streams.** `x-hive-streams` lists every live view with its path, capability,
listeners, first-frame schema and frame schema. Three of them matter to a client that submits and
answers:

| Stream | Capability | Frames |
|---|---|---|
| `/v1/push/stream` | `entrance:push` | `PushNotice`: the live push channel |
| `/v1/chat/stream` | `entrance:submit`, `C2` | `ChatFrame`: each new chat line; `?after=<seq>` resumes after a line, and without it only new lines arrive |
| `/v1/entrance/stream` | `observe` | `SecurityFrame`: every Entrance security event |

A browser cannot set headers on a WebSocket, so a socket authenticates with its **first frame**. The
frame is a `SocketHello` (`token`, `timestamp`, `nonce`, `signature`), and it must arrive within
`first_frame.deadline_s` (5 seconds). The signature is the binding key's, over `hive-ws-v1`, whose
fields are the socket's raw path and query exactly as requested, the timestamp and the nonce. Until
that frame is accepted, the socket reads nothing else. Nonce and timestamp follow the same rules as
requests. A browser's `Origin` must be the Entrance's own.

<!-- run: socket -->
```sh
"$HIVE_SIGN" socket device.pem token /v1/push/stream      # the first frame, as one line of JSON
```

Send that line as the socket's first text frame, from any WebSocket client:

```sh
{ "$HIVE_SIGN" socket device.pem token /v1/push/stream; cat; } |
  websocat "$(printf '%s' "$HIVE_URL" | sed 's/^http/ws/')/v1/push/stream"
```

The close codes are in the document's `x-hive-socket-close`. `1000` means the view ended or you
left. `1001` means the Entrance is shutting down. `1008` means the handshake itself was refused
before it was accepted (a foreign `Host` or forwarding header on loopback, or an address over its
rate). `4401` means the first frame failed or came late. `4403` means the device
lacks the view's capability. `4409` means you fell behind: reconnect from your cursor. `4410` means
the session ended (logout, expiry, idle, lock or revocation). `4411` means the Entrance was reduced
and every remote socket closed.

**Webhooks,** for a program that is not always connected. `POST /v1/push/subscriptions` takes a
`SubscribeBody` with `channel` `webhook` and your URL as `endpoint`. It needs `entrance:push` and
answers `201` with a `SubscriptionView`, whose `id` (`sub_...`) you keep with the URL.
`DELETE /v1/push/subscriptions/{subscription_id}` removes it and answers `204`. The URL must be
`https`, or resolve inside the overlay's `vpn_cidrs`, or be on the operator's
`[entrance.push] webhook_allowlist`. It never resolves to a loopback, link-local or unspecified
address, or to the Hive Stand's own, and redirects are not followed. Each delivery is an HTTP `POST`
of the `PushNotice` as JSON, carrying:

- `X-Hive-Event-Id`: the notice's `event_id`. Deduplicate on it: retries and every channel reuse it.
- `X-Hive-Timestamp`: when this attempt was signed, in integer Unix seconds. Every attempt is signed
  afresh.
- `X-Hive-Signature`: the Hive's Ed25519 signature, unpadded base64url, over `hive-webhook-v1`.
  Its fields are your subscription id, the event id, the `X-Hive-Timestamp` value, and the SHA-256
  of the body bytes you received, in lowercase hex.

Verify the signature with the key enrolment returned (`hive_public_key_hex`), which is also what
`GET /v1/push/hive-key` answers. Because the subscription id is signed, a notice captured at one
receiver cannot be replayed at another. **Not in the document:** a delivery is retried with
exponential backoff (2, 4, 8, 16 seconds, five attempts in all) after a network error or a `5xx`,
`408` or `429` answer. Any other `4xx` is final. Answer `2xx` once the notice is stored. The
document sets no freshness window for the timestamp. Refusing a delivery signed more than a few
minutes ago is reasonable, because retries are always freshly signed.

**Web Push,** for browsers and phones. `GET /v1/push/vapid-key` answers the
`application_server_key` for `PushManager.subscribe`. Register the resulting subscription with
`channel` `web_push`, its `endpoint`, and `keys` (`p256dh`, `auth`). Deliveries follow RFC 8030,
encrypted with RFC 8291 (`aes128gcm`) under RFC 8292 VAPID. The plaintext is the same `PushNotice`
JSON, padded to one size, so the push service learns neither the content nor the kind.

### 8.3 Answer

`GET /v1/inbox` (`entrance:answer`, `C2`) answers an `InboxView`: the `questions` waiting on the
human and the `alarms` that reached them, oldest first. A `QuestionView` has the question's `id`,
the `task_id` blocked on it, its `text` and its `options`. Empty `options` means a free-text answer.

<!-- run: answer -->
```sh
"$HIVE_SIGN" request device.pem GET /v1/inbox > signed.headers
curl -sS --fail-with-body --noproxy '*' "$HIVE_URL/v1/inbox" \
     -H @auth.header -H @signed.headers > inbox.json
jq -r '.questions[] | "\(.id): \(.text)"' inbox.json
```

Answer with `POST /v1/inbox/questions/{question_id}/answer`, whose `AnswerBody` holds the `text`
and, for a closed question, the `chosen_option` index. It answers `200` with an `AnsweredView`:
`question_status` is `ANSWERED`, and the task it unblocked runs again. A question already answered,
on this device or any other, is `409`. Acknowledge an Alarm with
`POST /v1/inbox/alarms/{alarm_id}/acknowledge`, which answers `200` with `AcknowledgedView`
(`acknowledged`).

<!-- run: answer -->
```sh
question=$(jq -r '.questions[0].id' inbox.json)
printf '%s' '{"text":"spring"}' > answer.json
target="/v1/inbox/questions/$question/answer"
"$HIVE_SIGN" request device.pem POST "$target" answer.json > signed.headers
curl -sS --fail-with-body --noproxy '*' -X POST "$HIVE_URL$target" \
     -H @auth.header -H @signed.headers -H 'Content-Type: application/json' \
     --data-binary @answer.json | jq -r .question_status
```

**The chat** is the human's end of the Queen's inbox, not a side channel. `POST /v1/chat` takes a
`ChatPost` (`text`, and optionally the `task_id` it concerns) and needs `entrance:submit`. It
answers `202` with the new line's `id`, and the Queen reads the message on her next tick.
`GET /v1/chat` (`entrance:submit`, `C2`) answers a `ChatPage`: `entries` oldest first, and
`newest_seq`. Page with `after`, `before` and `limit` (up to 500). Every `ChatLine` has an `author`
(`human` or `queen`) and a `kind` (`message`, `reply`, `question`, `alarm` or `notice`). Her
questions and the Alarms that reached the human are lines too, with the id to answer or acknowledge
in `ref`, so a chat view shows everything in one place.

<!-- run: chat -->
```sh
printf '%s' '{"text":"How did it go?"}' > chat.json
"$HIVE_SIGN" request device.pem POST /v1/chat chat.json > signed.headers
curl -sS --fail-with-body --noproxy '*' -X POST "$HIVE_URL/v1/chat" \
     -H @auth.header -H @signed.headers -H 'Content-Type: application/json' \
     --data-binary @chat.json | jq -r .id
```

When the Queen replies, a `reply_waiting` notice arrives. Then read the chat:

<!-- run: read-chat -->
```sh
"$HIVE_SIGN" request device.pem GET '/v1/chat?limit=20' > signed.headers
curl -sS --fail-with-body --noproxy '*' "$HIVE_URL/v1/chat?limit=20" \
     -H @auth.header -H @signed.headers | jq -r '.entries[] | "\(.author) \(.kind): \(.text)"'
```

## 9. The push contract

- **A notice says only that something is waiting, never what.** A `PushNotice` has four members:
  `event_id`, `kind`, `ref` (the id of the question, Alarm, goal request, chat line or security
  event it points at) and `created_at`. It carries no content, because it may transit a push
  service the Hive does not control. Fetch the item itself over your own session when the human
  looks.
- **Kinds** (`NoticeKind`). Every kind reaches only approved devices that hold `entrance:push`,
  through the device's live `/v1/push/stream` socket and its stored subscriptions. Within that:
  - `question_waiting` goes to devices holding `entrance:answer`.
  - `alarm_waiting` goes to every such device.
  - `reply_waiting` goes to devices that may read the chat (`entrance:submit` and
    `honey:clearance:c2`).
  - `goal_completed` goes only to the device that submitted the goal.
  - `security_event` goes to every such device except the one the event is about, so "a device is
    asking to join" never reaches the device that is asking.
  - `withdrawn` is described in the next item.
- **Answered anywhere, withdrawn everywhere.** When a question is answered, or an Alarm
  acknowledged, on any device, the Entrance sends a `withdrawn` notice with the same `ref` to every
  socket and subscription that received the original. The human never answers a question twice.
- **Idempotent by event id.** `event_id` is minted once and reused by every retry and every
  channel. Process a notice at most once per `event_id`.

## 10. Errors and refusals

Every refusal an operation declares carries an `ErrorBody`:

- `error`: a stable code, such as `hivemind.entrance.step_up_required`.
- `detail`: one sentence. It never echoes a credential or the request's input.
- `reason` and `pending_id`: for `step_up_required`.
- `capability`: for `capability_denied`.

| Status | Meaning | Codes a client will meet |
|---|---|---|
| `401` | Authentication failed: log in again with the device key and password. | `hivemind.entrance.authentication_failed` |
| `403` | Refused: a capability, a step-up, or the device's standing. | `capability_denied` (with `capability`), `step_up_required` (with `reason`, and `pending_id` for a held request), `enrolment_refused`, `confirmation_refused` |
| `404` | Not found, or not served on this listener. | `hivemind.entrance.not_found` for a route this listener does not serve |
| `409` | The item moved on meanwhile, such as a question already answered. | varies |
| `422` | A body or parameter is not valid. The detail names the fields, never their values. | `hivemind.entrance.invalid_request` |
| `429` | Too many requests from this device or address. | `hivemind.entrance.rate_limited` |

The document does not enumerate the codes. The ones above are what the Entrance answers today. The
`hivemind.entrance.` prefix is left off the table's `403` codes. Capability denials count toward a
burst lock: `[entrance] lockout_denials` of them inside `lockout_denial_window_s` (20 in 60 seconds
by default) lock the device.

Some refusals happen before routing and carry no body at all; the document lists them in
`x-hive-bare-refusals`:

- the loopback listener's `403` for a foreign `Host` or a forwarding header;
- the per-address `429` (`[entrance] rate_limit_per_address`);
- `413` for a body over 256 KiB;
- `408` for a body that did not arrive within 30 seconds.

A method a path does not take is `405`, with `hivemind.entrance.method_not_allowed` and an `Allow`
header. A failure inside the Hive is `500` with a code and a fixed sentence.

## 11. Versioning

- Every route lives under `/v1/`. Within `/v1/`, only additive changes are made: a new route, a new
  optional request member, a new response member, or a new member of an enum the document marks
  open.
- Anything else is breaking: removing or renaming a route or member, tightening a type, changing
  what a status means, or changing a signed string. A breaking change ships as `/v2/`, and `/v1/` is
  kept beside it for one Brood release.
- The document is generated from the Entrance's own route models and committed. The build fails
  when the two differ, so every change to the contract is visible in review.
- **Ignore response members you do not know.** Every object you read (a response body or a stream
  frame) is published open: its schema never says `additionalProperties: false`, because a later
  `/v1/` may add a member. The document's `x-hive-versioning` states the rule.
- **Enums.** An enum the document marks `x-hive-open` may gain members within `/v1/`: today the
  kinds of push notice, chat line, push channel, step-up action, model slot, device family,
  acceptance check and goal source. Treat a value you do not know as "something else" rather than
  an error. Every other enum (a state machine, a security tier) is closed until `/v2/`.
- **Send only what the document declares.** Request schemas refuse unknown members with `422`.
