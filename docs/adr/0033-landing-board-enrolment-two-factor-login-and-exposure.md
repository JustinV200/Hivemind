# ADR-0033: Devices enrol with their own key, log in with it plus the operator's password, and reach the Entrance over a VPN

- Status: Accepted
- Date: 2026-09-24

## Context

The Hive Entrance (ADR-0032) is the only door into the Hive, and it can spend money, run commands
on borrowed machines and read personal data. Brood 1.0 has exactly one human, the operator, and a
handful of clients: the Observation Hive in a browser, the `hive` CLI on a laptop, a phone, a
program. Codingrules 8.15 and 15 fix the goals: no sign-up, enrolment approved only at the Hive
Stand, two factors one of which is the device, sessions useless without the device, step-up for
anything sensitive, never on the open internet, and a way to slam the door. Three facts constrain
the design. Browsers expose passkeys (WebAuthn), WebCrypto and Web Push only in a secure context,
and WebAuthn refuses an IP address as a relying party. Bees on the Hive Stand run as the Hive's
own operating-system user, so anything the Hive keeps on disk in the clear, a bee can read. And
device ids are not secret (they appear on the trail and in `HumanMessage`), so nothing keyed only
on a device id may be allowed to hurt that device. The decisions below are hard to reverse
because every client implements them.

## Decision

**One operator, one password.** The Entrance tables hold one operator row with an Argon2id
password hash in PHC string form, derived with `cryptography`'s `Argon2id` (RFC 9106's second
recommended profile: 3 passes, 4 lanes, 64 MiB), so no password library is added. Hashing and
verifying run in a worker thread behind a semaphore of two, so a burst of logins can neither
block the Queen's event loop nor exhaust memory. OpenSSL runs every derivation's four lanes on one
thread pool per process, about one thread per core, which two derivations at once exhaust on a
machine with fewer than eight cores (a deadlock, or a spurious `MemoryError`), so each derivation
also takes one process-wide lock and runs alone. `hive entrance operator password` sets it on the
Hive Stand the first time and afterwards changes it only on presentation of the current one;
`--reset` works only while `hive serve` is stopped and revokes every device and session, because
it means the password itself is lost. Roadmap 14.2's `hive init` will call the same function.
`hive entrance operator add` exists and refuses unless `[entrance] operators` is raised above one,
which Brood 1.0 never does.

**Every client is a device with its own key, and the Entrance stores only public keys.** A device
holds either a WebAuthn passkey (a browser, or Android's credential manager in the Capacitor
build), created with user verification required and `none` attestation, verified with the
`webauthn` library; or an Ed25519 key (a program, the CLI), verified with `cryptography`. The
registration's backup flags are kept, so an approval shows when a passkey is synced (which makes
the passkey provider's account part of the factor). The Hive Stand's own console is a device too:
`hive entrance operator password` mints its Ed25519 key into the secret store **wrapped** under a
key derived from the operator password (Argon2id with its own salt, AES-256-GCM), records it
approved and **loopback-bound** (its sessions open only on the loopback listener), and keeps its
sessions in memory only. A bee that reads the secret store therefore holds nothing usable, and
the first remote device is approved without any special path.

**Bees never touch the Hive's own state.** A `[guard]` floor (ADR-0031) denies every bee principal
`fs:read` and `fs:write` on the Hive's state paths (the database and its WAL and SHM files, the
secret store, the manifest), `exec` of the `hive` and `hivemind-*` entry points, and `net` to any
loopback address or the Hive Stand's own addresses, whatever its role set says. This narrows what
an injected bee can do with its tools; it cannot stop a bee with an arbitrary `exec` capability on
a `FULL` Hive Stand from reaching the same files through a command, which is why that remains a
Capping-gated tier and why work that must not be trusted with the Hive Stand is placed with
`isolation = "required"`. Running Hive Stand bees under a separate operating-system user is
recorded as a risk, not solved here.

**Enrolment is invite, key, pending, approval on loopback.** `hive entrance invite --device
"phone"` mints a 128-bit single-use code, stored only as its SHA-256, valid for
`invite_ttl_minutes` (default 15), shown as grouped text and as a QR of the Entrance URL with the
code in the fragment. The device record is created in `INVITED`. The device redeems the code on
an unauthenticated, per-address rate-limited route with its public key (a WebAuthn registration
over a challenge the Entrance issued for that code, or an Ed25519 public key plus its signature
over `hive-enrol-v1`, the Hive id, the code's hash and the key) and a self-description; the
record moves to `PENDING`, the device shows its key's fingerprint, and every approved device
receives a `security_event` push ("a device is asking to join"). Approval binds the device's name,
its `CapabilitySet` (never wider than the `device` role's ceiling in `[guard]`, ADR-0031), a daily
spend cap, an expiry and whether it is **interactive** (a human types the password at it: true for
passkey devices and the console, false for program keys unless the operator says otherwise).
Every approval surface shows the key fingerprint and the backup flags and escapes every string
the device supplied. Denial refuses it; an unredeemed invite or an unapproved request expires
(`pending_ttl_hours`, default 24); an approved device expires at its expiry. **Invite minting,
approval, denial, unlock, capability widening, revocation, reopening and `operator add` are routes
that exist only on the loopback listener.** Locking a device is narrowing, so an interactive
device may lock another from the remote listener after step-up (a lost phone), and only loopback
unlocks it. Revocation lists the device's open goals and, with `--cancel-goals`, cancels them in
the same step; the revocation event names any goals left running. A steward device (`[entrance]
steward_devices = true`, off by default, holding `entrance:steward`) may approve through a
separate steward route that is mounted on the remote listener only when the switch is on, only
after full step-up, granting at most its own set intersected with the device ceiling and never
`entrance:steward` itself, and no higher daily spend cap and no later expiry than its own (a
grant never exceeds its grantor, ADR-0031); nothing else approves remotely. The state machine is
`entrance/enrol/state.py`: `INVITED → PENDING → APPROVED`, `INVITED → EXPIRED | REVOKED`,
`PENDING → DENIED | EXPIRED`, `APPROVED ↔ LOCKED`, `APPROVED | LOCKED → EXPIRED | REVOKED`; every
edge is a `guard.entrance_*` trail event (a trail kind has exactly one dot), and leaving
`APPROVED` ends the device's sessions and
deletes its push subscriptions in the same step.

**Login is the device key plus the password, the key proof first.** A device asks for a login
challenge (32 random bytes, single use, 60 s; a browser registers its session-binding public key
in the same request, which ties that key to the ceremony). It then proves possession of its device
key over the challenge (an Ed25519 signature over `hive-login-v1`, the Hive id, its device id and
the challenge, or a WebAuthn assertion with the challenge, user verification required, origin and
RP id checked, sign count not regressing) and presents the operator password in the same request.
The Entrance verifies the device proof first. An invalid proof never reaches the password check:
it counts against the requesting address's rate limit and is a signal for the Guard Bee, but not
against the device, because device ids are not secret and anyone could otherwise lock the
operator out. A valid proof with a wrong password is a failure of that device; `lockout_attempts`
of them in a row lock it. A failure never says which factor failed.

**Sessions are bound to a key, and every request is signed.** A session is a random 256-bit bearer
token the Entrance stores only as a SHA-256 hash, carrying its device, its **binding key**, the
listener it was opened on, `session_ttl_hours` and `idle_timeout_minutes`. The binding key is the
device's Ed25519 key for a program; for a browser it is a non-extractable WebCrypto ECDSA P-256 key
kept in IndexedDB and deleted at logout. Every authenticated request carries `Authorization:
Bearer <token>`, `X-Hive-Timestamp`, `X-Hive-Nonce` and `X-Hive-Signature`: the binding key's
signature over `hive-request-v1`, the method, the raw path and query exactly as sent, the
timestamp, the nonce and the body's SHA-256. The exact strings and encodings (integer Unix
seconds, lowercase hex digests, base64url without padding for nonces and signatures, P-256
signatures in WebCrypto's IEEE P1363 form) live in one module, `entrance/auth/canonical.py`, and
are published in the OpenAPI document (`securitySchemes` plus an `x-hive-signing` extension) so a
program written from the document alone can sign. The Entrance refuses a request whose token is
unknown, expired or idle, whose signature does not verify, whose timestamp is more than
`request_skew_s` from the Hive Stand's clock, whose nonce it has seen (nonces are kept, persisted,
for twice the skew window, so a restart does not reopen a replay window), or which arrived on a
different listener than the session was opened on. A stolen token alone is useless. A WebSocket
authenticates with a first frame carrying the token and a signature over `hive-ws-v1`, the path,
a timestamp and a nonce under the same rules, within five seconds or it is closed; a browser's
`Origin` must be the Entrance's own; and every socket of a session closes when the session ends
for any reason (logout, expiry, revocation, lock, reduction).

**Step-up needs a human.** A step-up is a fresh passkey assertion with user verification, or the
device proof plus the password typed at an interactive device; it marks the session stepped up
for `step_up_window_minutes`. It is required for: a goal whose budget exceeds `step_up_spend`; a
goal that would take the submitting device past its daily spend cap; key and capability changes;
Supersedure; Sting Cut; Absconding; reopening a reduced Entrance; locking another device.
Break-glass actions (Absconding, Sting Cut, Supersedure) additionally require the typed
confirmation phrase of codingrules 15 in the request, and only from an interactive device. A
non-interactive device cannot step up: a request of its that needs step-up gets `403
step_up_required` with the id of a **pending confirmation**, which is pushed to the human and
carried out only when an interactive device steps up and confirms it. A program's goal above its
spend cap is therefore refused pending a human, never by its own two factors.

**Lockout, rate limits, travel lock.** A device is `LOCKED` after `lockout_attempts` valid-proof
login failures, or `lockout_denials` capability denials inside `lockout_denial_window_s` (a
submit-only program suddenly calling observe routes), until a loopback unlock. The console
itself, if locked, is unlocked offline with `hive entrance unlock --console` and the password
while `hive serve` is stopped. Token buckets limit requests per device (`rate_limit_per_device`
per minute) and per address (`rate_limit_per_address`, which also covers every unauthenticated
route). The travel lock (`travel_lock`, off by default) works only with `expose = "vpn"` on
Tailscale, because an overlay address never changes when its device roams: the Entrance asks
tailscaled's local API for the peer's current endpoint, a device seen from a network it has not
used before must step up, and every other device is notified. It refuses to start anywhere it
cannot see real endpoints, and it never approves anything.

**Exposure never means the open internet, and remote always means TLS on a name.** `[entrance]
bind` is a loopback address and the loopback listener is always on. It answers only requests
whose `Host` is a loopback name or address, and refuses any request carrying `Forwarded`,
`X-Forwarded-*`, `X-Real-IP` or `Tailscale-*` headers, so a proxy (`tailscale serve`, a reverse
proxy, a DNS-rebinding page) can never front the routes that approve devices; the client guide
forbids proxying it. The Hive Stand's own browser reaches it as `http://localhost:<port>`, a
secure context whose relying party is `localhost`. `entrance/expose.py` reads `[entrance] expose`:

- `loopback` (default): no remote listener.
- `vpn` (recommended): the remote listener binds `remote_bind`, a specific address that must be
  assigned to the named `vpn_interface` and fall inside `vpn_cidrs`. The interface check is what
  counts: Tailscale's IPv4 range is also carrier-grade NAT space an ordinary WAN address can be in.
  The documented overlay is **Tailscale** (WireGuard underneath, NAT traversal, stable per-device
  addresses, MagicDNS names, clients on Windows, Ubuntu, Arch and Android); plain WireGuard works
  by naming its interface and subnet.
- `lan` and `tunnel`: mutual TLS on top. The Hive runs its own certificate authority
  (`entrance/tls/`, its key in the secret store). A device enrols over loopback or `vpn`, or
  offline (the operator registers the device's public key and certificate request on the Hive
  Stand), and receives its client certificate at approval: signed from a CSR for a program, as a
  PKCS#12 bundle for a browser. The remote listener requires a certificate chaining to the Hive's
  authority; a revocation rebuilds the TLS context, which new handshakes pick up through an
  `sni_callback`. uvicorn does not expose the peer certificate to the application, so mutual TLS
  is the outer gate and login the inner one. `tunnel` binds the remote listener to loopback for a
  TCP-forwarding client (its argv in `tunnel_command`, so TLS stays end to end; any token it needs
  in `HIVEMIND_ENTRANCE_TUNNEL_*`, handed to the child's environment only) run as a supervised
  child by `hivemind.entrance.expose.tunnel`, the one Entrance module codingrules 4 lets start a
  process.
- Every remote mode requires TLS on a DNS name, and the Entrance refuses to start exposed without
  it: browsers need a secure context and a registrable domain as the WebAuthn relying party
  (Tailscale's MagicDNS name with `tailscale cert`, or the operator's own domain). `[entrance]
  rp_id` (default: `public_url`'s host) is fixed when the first remote device enrols and moves with
  the Hive Stand on Supersedure, because a passkey is bound to it. There is no `public` value; the
  schema rejects it. CORS is enabled only for `public_url`. The Android build's origin and its
  asset-links file are decided by the Android ADR in phase 12.

**Browsers get nothing that can run foreign script in the Entrance's origin.** FastAPI's
interactive documentation routes are disabled (they load script from a CDN); the committed
OpenAPI document is served as a static file; every response carries `Content-Security-Policy:
default-src 'self'; object-src 'none'; frame-ancestors 'none'`.

**The Entrance Reducer.** `entrance/reducer.py` holds `EntranceMode` (`OPEN ↔ REDUCED`), persisted
in the Entrance tables so a restart comes back in the mode it left. Reducing stops the remote
listener and the tunnel child, revokes every session opened on the remote listener and closes
their WebSocket streams within one second. `hive entrance reduce` on the Hive Stand reduces; so
does the Guard Bee, by recording a `guard.reduce_ordered` trail event the Entrance follows
(ADR-0035), because narrowing access is always safe to do without judgement and the Guard, a
Worker, cannot import the Entrance; so does a remote listener that fails to bind or crashes, which
also raises an Alarm and never stops the Queen. The Guard's lockout rule counts only valid-proof
lockouts, so garbage logins cannot be used to slam the door on the operator. Reopening is a
loopback-only route and needs step-up.

## Consequences

Positive: a stolen password is useless without an enrolled device, and a stolen device, token or
secret-store file is useless without the password; a fully compromised remote session still
cannot admit a device, widen a capability or reopen the door, because those routes do not exist
where it is; a program cannot talk itself past its spend cap. The overlay keeps the Entrance
invisible to the internet by default. Every client, the Hive Stand's own console included, walks
the same login path, and the signing rules are in the published contract.

Negative: every client must sign every request, which rules out plain `curl` without a helper (the
client guide ships one); browsers need passkeys, WebCrypto and a TLS name even on the overlay;
`lan` and `tunnel` need a client certificate on every device, which is why `vpn` is the
recommended path. Argon2id at 64 MiB makes each login cost a fraction of a second, which the
semaphore and rate limits keep from becoming a lever. The operator's password is shared by every
device, so a program holds it too; Brood 1.0 accepts that for a single operator. Bees on the Hive
Stand still share its operating-system user, so the state-path floor narrows but does not close
what an arbitrary command could reach.

## Alternatives considered

Shared API keys for programs: one leaked key is the whole Hive, and nothing binds it to a device.
Password-only or passkey-only login: one factor. Bearer tokens without request signing: a leaked
token is a full session. Cookie sessions with CSRF tokens: browser-only, and still not bound to a
key. Counting every failed login against the device: lets anyone who reads a device id off the
trail lock the operator out. Loopback-only routes filtered by client IP on one listener: one
mis-ordered middleware exposes approval remotely; two listeners make the route absent. Plain HTTP
over the overlay: browsers refuse passkeys and WebCrypto there. WireGuard as the documented
overlay: equally secure, but NAT traversal, names and phone setup are manual, so it is the
supported alternative rather than the default. A `public` mode behind TLS alone: exactly the
exposure the codingrules forbid.
