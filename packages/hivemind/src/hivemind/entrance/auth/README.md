# hivemind.entrance.auth

The credential primitives the Hive Entrance's login is built from. Login is two factors, the
device's own key and the operator's password, and every authenticated request is signed by a key
bound to its session (ADR-0033). Each module here is pure or a thin wrapper; sessions and
step-up (roadmap 10.5e) are built on top of them, and login reuses the challenge book passkey
enrolment already uses.

| Module | What it does |
|---|---|
| `canonical.py` | The one place every signed string and encoding is defined: `enrol_string`, `login_string`, `request_string`, `websocket_string`, `webhook_string`; base64url without padding (`b64url_encode`/`b64url_decode`, canonical spellings only), `sha256_hex`, `new_nonce`, `path_and_query`. Clients reuse it. |
| `keys.py` | `KeyKind` (`ED25519` for programs and the CLI, `PASSKEY` for browsers); `verify_ed25519` and `verify_p256` (a browser's WebCrypto session key: uncompressed point, IEEE P1363 `r||s`), both returning False rather than raising; `key_fingerprint`, the grouped digest a device and every approval surface show. |
| `password.py` | `PasswordHasher`: Argon2id (RFC 9106's second profile: 3 passes, 4 lanes, 64 MiB) in worker threads behind a semaphore of two, so a burst of logins can neither block the event loop nor exhaust memory. Passwords are NFKC-normalised and must be 12 to 1024 characters. |
| `wrap.py` | `wrap_private_key`/`unwrap_private_key`: a private key sealed with AES-256-GCM under a key derived from the operator password (Argon2id, its own salt), bound to its secret's name; every failure raises the same `KeyUnwrapError`. |
| `passkeys.py` | WebAuthn through the `webauthn` library: registration and authentication options (user verification required, `none` attestation, resident key preferred, EdDSA/ES256/RS256) and their verification, refusing a sign count that does not move forward. `RelyingParty` refuses an IP address: WebAuthn does, and browsers need a secure context, so the Hive Stand uses `http://localhost` and remote browsers an https DNS name. |
| `challenges.py` | `ChallengeBook`: single-use challenges (32 random bytes), each bound to a subject (an invite's code hash, later a device id) and optionally to a binding public key, with a lifetime, taken once, expired lazily and capped at `MAX_OPEN_CHALLENGES` (the oldest evicted first) so an unauthenticated flood cannot grow memory. In memory by design: a restart only forces a new ceremony. Every refusal is the same `ChallengeRejectedError`. |
| `fake.py` | `SoftPasskey`: a software authenticator that answers creation and request options like a browser, with knobs for origin, user verification, backup flags and the counter. |

## Public API

`KeyKind`, `verify_ed25519`, `verify_p256`, `key_fingerprint`; `PasswordHasher`,
`check_password_strength`, `MIN_PASSWORD_CHARS`, `MAX_PASSWORD_CHARS`; `wrap_private_key`,
`unwrap_private_key`; `RelyingParty`, `PasskeyRegistration`, `StoredPasskey`,
`registration_options`, `authentication_options`, `registration_challenge` (the challenge a
registration claims to answer, for the book lookup), `verify_registration`,
`verify_authentication`; `ChallengeBook`, `Challenge`, `MAX_OPEN_CHALLENGES`; `SoftPasskey`; and from `canonical`: the five string builders, their tags, `b64url_encode`,
`b64url_decode`, `sha256_hex`, `new_nonce`, `path_and_query`, `MIN_NONCE_BYTES`, `NONCE_BYTES`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/auth
```

`test_canonical.py` pins a fixed vector for every signed string, and a deterministic Ed25519
signature over the login string, so a client written in another language can check itself
against the same bytes. `test_password.py` proves the semaphore bound by holding four hashes in
flight and counting how many reach the worker threads at once.
