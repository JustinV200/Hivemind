# hivemind.entrance

The Hive Entrance is the only door into the Hive: two listeners, loopback (always on) and remote
(only when explicitly exposed), serving the Landing Board (the versioned public API contract),
device enrolment, auth, push delivery, exposure control and the human inbox. Approval routes never
exist on the remote listener. Every client, the Hive Stand's own console included, is a device
enrolled with its own key and approved at the Hive Stand, and logs in with that key plus the
operator's password (codingrules 8.15, `docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md`).

Roadmap steps 10.4, 10.5a (remote exposure), 10.5b (push), 10.5d (device enrolment, both
halves) and 10.5e (login, sessions, step-up and the Entrance Reducer) landed what is below; the
listeners and routes are later steps of phase 10.

## Layout

| Package | What it holds |
|---|---|
| `errors.py` | `EntranceError` and every refusal, each also in its `hivemind.common.errors` category. |
| `auth/` | Credential primitives (the signed strings, device keys, networks, Argon2id passwords, the wrapped console key, WebAuthn, the `ChallengeBook`, `SoftPasskey`) and the flows built on them: `session/` (sessions bound to a key, every request signed, a socket's first frame), `login/` (the key proof first, then the password; lockout), `step_up/` (when, how, break-glass phrases), `confirm/` (pending confirmations), `limits/` (rate limits, the denial-burst lock), `travel/` (the travel lock over tailscaled). |
| `enrol/` | Device enrolment: the enrolled-device model and its state machine, the operator/console bootstrap, invites, redemption by Ed25519 key or passkey, approval and denial, revocation, lock, unlock and the expiry sweep, the device-ceiling and steward rules, and the seams later steps implement. |
| `reducer.py` | The Entrance Reducer: `EntranceMode` (`OPEN` and `REDUCED`, persisted), `reduce` (ends every remote session, stops the remote listener, closes remote sockets), `reopen` (loopback only, after step-up), `start_mode` (a restart comes back reduced). |
| `store/` | The Entrance tables: `EntranceStore`, its SQLite and in-memory implementations, and the sessions, logins, pending-confirmation and mode tables; every status change is written with its `guard.*` trail event in one transaction. |
| `push/` | The push channels (roadmap 10.5b); its own README. |
| `expose/` | Remote exposure (roadmap 10.5a): the pure mode check over gathered host facts (vpn, lan, tunnel; never public), the Hive's own certificate authority and the mutual-TLS context rebuilt on every revocation, the supervised tunnel client, the loopback listener's Host and forwarding-header check; its own README. |
| `routes/` | A skeleton, populated when the Entrance app and its listeners land. |

## Public API

The face (`hivemind.entrance`) re-exports what most callers need; each sub-package's own face
lists the rest.

- `EntranceError` and its subclasses: every refusal the Entrance makes on purpose.
- `KeyKind`, `PasswordHasher`, `RelyingParty`, `SoftPasskey`, `ChallengeBook`: the credential
  primitives.
- `DeviceStatus`, `EnrolledDevice`, `DeviceDescription`, `DeviceInvite`, `OperatorCredential`:
  the enrolled-device model and its state machine.
- `ConsoleDeps`, `bootstrap_operator`, `change_operator_password`, `unlock_console_key`: the
  operator and the Hive Stand console.
- `EnrolmentDeps`, `EntranceIdentity`, `mint_invite`, `cancel_invite`, `passkey_options`,
  `redeem_ed25519`, `redeem_passkey`, `Ed25519Proof`, `ApprovalRequest`, `approve`, `deny`,
  `revoke`, `lock`, `unlock`, `LockReason`, `expire_due`, `steward_grant`: device enrolment.
- `EntranceStore`, `SqliteEntranceStore`, `MemoryEntranceStore`, `SplitSessionTable`: the
  Entrance tables.
- `SessionBook`, `SessionRules`, `AuthDeps`, `begin_login`, `finish_login`,
  `authenticate_request`, `authenticate_websocket`, `step_up`, `requires_step_up`, `hold`,
  `confirm`: login, sessions, step-up and pending confirmations.
- `EntranceReducer`, `EntranceMode`, `ReduceReason`, `ReducerSeams`, `RemoteListenerControl`,
  `StreamCloser`: the Entrance Reducer.

Nothing in the Entrance stores a password, an invite code, a session token or a private key in
the clear: the tables hold an Argon2id hash and public keys, invites and sessions are stored as
the SHA-256 of their code or token, and the console's private key sits in the secret store sealed
under the operator password (its sessions are kept in memory only). No
trail event carries a code, a key, a signature or a password either; a test walks a whole
enrolment lifecycle and checks every event for each of them.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance \
    packages/hivemind/tests/contracts/test_entrance_store_contract.py \
    packages/hivemind/tests/contracts/test_entrance_session_table_contract.py \
    packages/hivemind/tests/contracts/test_entrance_auth_tables_contract.py
```

The password, wrapping and console tests run the real 64 MiB Argon2id (about a tenth of a second
per derivation), so they take a few seconds. `tests/unit/entrance/enrol/test_flow.py` runs the
whole lifecycle (console bootstrap, a program and a browser enrolling, approval, lock, unlock,
revocation, expiry, a refused replay) over in-memory tables and over one real SQLite file;
`tests/unit/entrance/auth/test_flow.py` does the same for authentication (the real console
bootstrap, logins, a held request confirmed after a real step-up, a reduction, a restart that
comes back reduced, a reopening). Passkey tests need no browser: `SoftPasskey` answers the
Entrance's options exactly as a browser's `PublicKeyCredential.toJSON()` would, and the real
`webauthn` verification checks it.
