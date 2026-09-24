# hivemind.entrance.auth

Authentication at the Hive Entrance. Login is two factors, the device's own key and the operator's
password, the key proof first; every authenticated request is signed by a key bound to its
session; sensitive actions need a step-up a person performs (ADR-0033). The modules directly here
are the credential primitives, pure or thin, calling nothing else in the Entrance; the
sub-packages are the flows built on them and on the enrolled-device model (roadmap 10.5e).

## Primitives

| Module | What it does |
|---|---|
| `canonical.py` | The one place every signed string and encoding is defined: `enrol_string`, `login_string`, `request_string`, `websocket_string`, `webhook_string`; base64url without padding (`b64url_encode`/`b64url_decode`, canonical spellings only), `sha256_hex`, `new_nonce`, `path_and_query`. Clients reuse it. |
| `keys.py` | `KeyKind` (`ED25519` for programs and the CLI, `PASSKEY` for browsers); `verify_ed25519` and `verify_p256` (a browser's WebCrypto session key: uncompressed point, IEEE P1363 `r||s`), both returning False rather than raising; `is_p256_point`; `key_fingerprint`. |
| `network.py` | A device's network (`address_network`: the /24 or /64; `relay_network`: `derp:<region>` for a relayed Tailscale peer; `is_device_network`) and `trail_address`, the address as the trail may carry it. |
| `password.py` | `PasswordHasher`: Argon2id (RFC 9106's second profile: 3 passes, 4 lanes, 64 MiB) in worker threads behind a semaphore of two. Passwords are NFKC-normalised and must be 12 to 1024 characters. |
| `wrap.py` | `wrap_private_key`/`unwrap_private_key`: a private key sealed with AES-256-GCM under a key derived from the operator password; every failure raises the same `KeyUnwrapError`. |
| `passkeys.py` | WebAuthn through the `webauthn` library: registration and authentication options (user verification required) and their verification, refusing a sign count that does not move forward. `RelyingParty` refuses an IP address. |
| `challenges.py` | `ChallengeBook`: single-use challenges (32 random bytes), each bound to a subject and optionally to a binding key, expired lazily and capped at `MAX_OPEN_CHALLENGES`. In memory by design: a restart only forces a new ceremony. |
| `fake.py` | `SoftPasskey`: a software authenticator that answers creation and request options like a browser. |

## Flows (roadmap 10.5e)

```text
 begin_login ──► challenge (60 s, bound to the device and a browser's P-256 key)
 finish_login ─► take challenge ─► device proof? ──no──► refused, charged to the ADDRESS
                                        │yes
                                        ▼
                                   password? ──no──► refused, counted against the DEVICE
                                        │yes                 (lockout_attempts in a row: lock)
                                        ▼
                     record login, travel lock, open a session bound to a key ─► token, once
 every request ─► Bearer token + X-Hive-Timestamp/Nonce/Signature ─► authenticate_request
 step_up (interactive only) ─► stepped up for step_up_window; a non-interactive device's
                               request is held as a pending confirmation instead
```

| Package | What it holds |
|---|---|
| `session/` | `models` (`Session`, `Arrival`, `AuthenticatedSession`, `NonceClaim`, `Listener`, `BindingKind`, `EndReason`), `token` (256-bit tokens, stored only as their SHA-256), `book` (`SessionBook`: open, judge alive, end; the sessions half of the enrolment step's `DeviceOffboarder`; `end_remote` for the Reducer; `revalidate` at start), `request` (`authenticate_request`, `authenticate_websocket`: token, liveness, device still APPROVED, signature under the binding key, listener, skew, persisted nonce, a browser socket's `Origin`), `failures` (`guard.entrance_login_failed` with a reason category, never a credential). |
| `login/` | `deps` (`AuthDeps`: enrolment, session book, ceremony, guards), `factors` (the Ed25519 or WebAuthn proof, the binding key, the password), `refusals` (who a failure counts against, and the lockout through `enrol.standing.lock`), `flow` (`begin_login`, `finish_login`). |
| `step_up/` | `rules` (`requires_step_up`: ADR-0033's list, the travel lock's flag first), `ceremony` (`step_up_challenge`, `step_up`), `phrases` (break-glass phrases, `check_break_glass`). |
| `confirm/` | Pending confirmations: `state` (`PENDING` settled once), `models`, `flow` (`hold`, `confirm`, `cancel`, `expire_pending`). |
| `limits/` | `RateLimiter` (token buckets per device and per address) and `DenialCounter` (a burst of capability denials locks the device); both in memory by design. |
| `travel/` | `PeerEndpointSource`, `TailscaleEndpointSource` (tailscaled's local API over its Unix socket), `FakePeerEndpointSource`, `TravelLock` and `open_travel_lock` (refuses to build outside `vpn` mode, on Windows, or without the socket). |

## What never leaves

Tokens exist only in the `OpenedSession` returned once by login (its `repr` hides the token);
the tables hold their SHA-256. No trail event, log line or error carries a token, a password, a
device proof, a signature or a header value; every refused authentication is the same
`AuthenticationFailedError`, and its reason category goes to the trail. The Hive Stand console's
sessions are volatile: `SplitSessionTable` keeps them in memory and the durable table refuses
one.

## Public API

The face (`hivemind.entrance.auth`) re-exports the primitives and the main names of every
sub-package: sessions (`Session`, `Arrival`, `AuthenticatedSession`, `SessionBook`, `SessionRules`,
`SessionGrant`, `OpenedSession`, `SignedRequest`, `SocketOpening`, `authenticate_request`,
`authenticate_websocket`, `SOCKET_HELLO_DEADLINE_S`), login (`AuthDeps`, `LoginCeremony`,
`LoginGuards`, `DeviceProof`, `LoginChallenge`, `begin_login`, `finish_login`,
`LOGIN_CHALLENGE_TTL`), step-up (`ActionKind`, `StepUpReason`, `GoalSpend`, `requires_step_up`,
`step_up`, `step_up_challenge`, `check_break_glass`, `BREAK_GLASS_PHRASES`), confirmations
(`PendingConfirmation`, `PendingStatus`, `HeldAction`, `hold`, `confirm`, `cancel`,
`expire_pending`), limits (`RateLimiter`, `DenialCounter`) and the travel lock (`TravelLock`,
`open_travel_lock`, `PeerEndpointSource`, `TailscaleEndpointSource`, `FakePeerEndpointSource`,
`tailscale_source`). Each sub-package's face lists the rest.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/auth \
    packages/hivemind/tests/contracts/test_entrance_session_table_contract.py
```

`test_canonical.py` pins a fixed vector for every signed string. `session/test_request.py` holds
roadmap 10.5e's stolen-token test and every request refusal (a replayed nonce also after the
tables are reopened). `login/test_flow.py` proves an invalid proof never reaches the password
check (the builders' `CountingHasher` counts verifications) and that five valid-proof wrong
passwords lock. `test_flow.py` runs the whole lifecycle over one SQLite file with the real
console bootstrap and the real 64 MiB Argon2id. The login rig stores the operator password
under a real Argon2id hash with the smallest legal cost, so every other test verifies it in
milliseconds.
