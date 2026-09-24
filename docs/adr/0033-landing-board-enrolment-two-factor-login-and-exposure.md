# ADR-0033: Devices enrol with their own key, log in with it plus the operator's password, and reach the Entrance over a VPN

- Status: Proposed
- Date: 2026-09-24

## Context

The Hive Entrance (ADR-0032) is the only door into the Hive, and it can spend money, run commands
on borrowed machines and read personal data. Brood 1.0 has exactly one human, the operator, and a
handful of clients: the Observation Hive in a browser, the `hive` CLI on a laptop, a phone, a
program. Codingrules 8.15 and 15 fix the goals: no sign-up, enrolment approved only at the Hive
Stand, two factors one of which is the device, sessions useless without the device, step-up for
anything sensitive, never on the open internet, and a way to slam the door. The decisions below
are hard to reverse because every client implements them.

## Decision

**One operator, one password.** The Entrance tables hold one operator row with an Argon2id
password hash in PHC string form, derived with `cryptography`'s `Argon2id` (RFC 9106's second
recommended profile: 3 passes, 4 lanes, 64 MiB), so no password library is added. The password
is set on the Hive Stand with `hive entrance operator password` (roadmap 14.2's `hive init` will
call the same function). `hive entrance operator add` exists and refuses unless
`[entrance] operators` is raised above one, which Brood 1.0 never does.

**Every client is a device with its own key, and the Entrance stores only public keys.** A device
holds either a WebAuthn passkey (a browser, or Android's credential manager in the Capacitor
build), created with user verification required and `none` attestation, verified with the
`webauthn` library; or an Ed25519 key (a program, the CLI), verified with `cryptography`. The
Hive Stand's own console is a device too: `hive entrance operator password` mints its Ed25519
key into the Hive's state directory with owner-only permissions and records it approved and
**loopback-bound** (its sessions can only be opened on the loopback listener), which is how the
first remote device ever gets approved without a special path.

**Enrolment is invite, key, pending, approval on loopback.** `hive entrance invite --device
"phone"` mints a 128-bit single-use code, stored only as its SHA-256, valid for
`invite_ttl_minutes` (default 15), shown as grouped text and as a QR of the Entrance URL with the
code in the fragment. The device record is created in `INVITED`. The device redeems the code on
an unauthenticated, per-address rate-limited route with its public key (a WebAuthn registration
over a challenge the Entrance issued for that code, or an Ed25519 public key plus a signature over
the code proving possession) and a self-description; the record moves to `PENDING` and every
approved device receives a `security_event` push ("a device is asking to join"). Approval binds
the device's name, its `CapabilitySet` (validated against `[entrance] device_capability_ceiling`
and never wider than it), a daily spend cap and an expiry; denial refuses it; an unredeemed invite
or an unapproved request expires (`pending_ttl_hours`, default 24). Approval, denial, unlock,
capability widening, reopening and `operator add` are routes that exist only on the loopback
listener. The state machine is `entrance/enrol/state.py`: `INVITED → PENDING → APPROVED`,
`INVITED → EXPIRED`, `PENDING → DENIED | EXPIRED`, `APPROVED ↔ LOCKED`, `APPROVED | LOCKED →
REVOKED`; every edge is a `guard.entrance.*` trail event. Revocation lists the device's open goals
and, with `--cancel-goals`, cancels them in the same step; the revocation event names any goals
left running. A steward device (`[entrance] steward_devices = true`, off by default, and the
device holding `entrance:steward`) may approve through a separate steward route mounted on the
remote listener only when the switch is on, and only after full step-up; nothing else approves
remotely.

**Login is the device key plus the password, and the session is bound to a key.** A device asks
for a login challenge (32 random bytes, single use, 60 s), then proves possession of its device
key over it (an Ed25519 signature over `"hive-login-v1\n<device_id>\n<nonce>"`, or a WebAuthn
assertion with the nonce as the challenge, user verification required, origin and RP id checked,
sign count not regressing) and presents the operator password in the same request. Both factors
are checked every time; a failure never says which. A session is a random 256-bit bearer token
the Entrance stores only as a SHA-256 hash, carrying its device, its **binding key**, the
listener it was opened on, `session_ttl_hours` and `idle_timeout_minutes`. The binding key is the
device's Ed25519 key for a program; for a browser it is a non-extractable WebCrypto ECDSA P-256
key the page generates for the session and registers when it asks for the challenge, so the key is
tied to that ceremony. Every authenticated request carries `Authorization: Bearer <token>`,
`X-Hive-Timestamp`, `X-Hive-Nonce` and `X-Hive-Signature`, the binding key's signature over
`"hive-request-v1\n<METHOD>\n<path?query>\n<timestamp>\n<nonce>\n<sha256(body)>"`; the Entrance
refuses a request whose token is unknown, expired or idle, whose signature does not verify under
the session's binding key, whose timestamp is more than 60 s from the Hive Stand's clock, whose
nonce it has already seen, or which arrived on a different listener than the session was opened
on. A stolen token alone is therefore useless. WebSocket streams authenticate with a first frame
carrying the same fields signed over `"WS <path>"`, since a browser cannot set headers on a
WebSocket, and the socket receives nothing until it does.

**Step-up re-runs both factors.** A successful step-up marks the session stepped up for
`step_up_window_minutes`. Required for: a goal whose budget exceeds `step_up_spend`; key and
capability changes; Supersedure; Sting Cut; Absconding; reopening a reduced Entrance.
Break-glass actions (Absconding, Sting Cut, Supersedure) additionally require the typed
confirmation phrase of codingrules 15 in the request body on every path, the API included. A
device's daily spend cap is a hard limit that step-up never lifts: a goal that would exceed it is
refused until the operator raises the cap on loopback.

**Lockout, rate limits, travel lock.** `lockout_attempts` consecutive failed logins, or
`lockout_denials` capability denials inside `lockout_denial_window_s` (a submit-only program
suddenly calling observe routes), move the device to `LOCKED` until a loopback unlock. Token
buckets limit requests per device (`rate_limit_per_device` per minute) and per address
(`rate_limit_per_address`, which also covers every unauthenticated route). With `travel_lock` on,
a known device seen from a network it has not used before (the /24 of an IPv4 address, the /64 of
an IPv6 one) must step up before its session proceeds and every other device is notified; the
travel lock never approves anything.

**Exposure never means the open internet.** `entrance/expose.py` reads `[entrance] expose`:

- `loopback` (default): only the loopback listener on `bind`, which must be a loopback address and
  is always on in every mode.
- `vpn` (recommended): the remote listener binds `remote_bind`, which must be a specific address
  (never a wildcard), assigned to a local interface, and inside `[entrance] vpn_cidrs`. The
  documented overlay is **Tailscale** (WireGuard underneath, NAT traversal, stable per-device
  addresses, clients on Windows, Ubuntu, Arch and Android), so `vpn_cidrs` defaults to its ranges
  (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`); plain WireGuard works by listing its own subnet. The
  overlay encrypts, so TLS is optional; unauthenticated packets never reach the Entrance because
  nothing listens anywhere else.
- `lan` and `tunnel`: TLS and mutual TLS are mandatory and the Entrance refuses to start without
  both. The Hive runs its own certificate authority (`entrance/tls/`): an approved device may
  submit a certificate signing request and receives a short-lived client certificate (90 days)
  naming its device id, the remote listener requires a client certificate chaining to that
  authority, and a revoked device's certificate is published on the CRL the listener checks.
  Login still happens on top. `tunnel` runs a configured TCP-forwarding client (its argv in
  `[entrance] tunnel_command`, so TLS stays end to end) as a supervised child of the Entrance,
  restarted with backoff and stopped with the remote listener.
- There is no `public` value; the schema rejects it. Per-device rate limiting always applies;
  CORS is enabled only for `public_url`.

**The Entrance Reducer.** `entrance/reducer.py` holds `EntranceMode` (`OPEN ↔ REDUCED`).
Reducing stops the remote listener and the tunnel child, revokes every session opened on the
remote listener and closes their WebSocket streams within one second. `hive entrance reduce` on
the Hive Stand reduces, and so does a Guard Bee autopilot rule (failure bursts, lockouts across
devices, an unknown client hammering the invite route), because narrowing access is always safe
to do without judgement. Reopening is a loopback-only route and needs step-up.

## Consequences

Positive: a stolen password is useless without an enrolled device, and a stolen device or token
is useless without the password; a fully compromised remote session still cannot admit a device,
widen a capability or reopen the door, because those routes do not exist where it is. The overlay
keeps the Entrance invisible to the internet by default. Every client, the Hive Stand's own
console included, walks the same login path.

Negative: every client must sign every request, which rules out plain `curl` without a helper
(the client guide ships one); browsers need WebCrypto and passkeys; `lan` and `tunnel` need a
client certificate on every device, which is why `vpn` is the recommended path. Argon2id at 64 MiB
makes each login cost a fraction of a second on the Hive Stand, which the rate limits keep from
becoming a denial-of-service lever. The operator's password is shared by every device, so a
program holds it too; Brood 1.0 accepts that for a single operator.

## Alternatives considered

Shared API keys for programs: one leaked key is the whole Hive, and nothing binds it to a device.
Password-only or passkey-only login: one factor. Bearer tokens without request signing: a leaked
token is a full session. Cookie sessions with CSRF tokens: browser-only, and still not bound to a
key. Loopback-only routes filtered by client IP on one listener: one mis-ordered middleware
exposes approval remotely; two listeners make the route absent. WireGuard as the documented
overlay: equally secure, but NAT traversal and phone setup are manual, so it is the supported
alternative rather than the default. A `public` mode behind TLS alone: exactly the exposure the
codingrules forbid.
