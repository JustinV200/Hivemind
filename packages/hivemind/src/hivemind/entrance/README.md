# hivemind.entrance

The Hive Entrance is the only door into the Hive: two listeners, loopback (always on) and remote
(only when explicitly exposed), serving the Landing Board (the versioned public API contract),
device enrolment, auth, push delivery, exposure control and the human inbox. Approval routes never
exist on the remote listener. Every client, the Hive Stand's own console included, is a device
enrolled with its own key and approved at the Hive Stand, and logs in with that key plus the
operator's password (codingrules 8.15, `docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md`).

Roadmap step 10.4 and step 10.5d (device enrolment, both halves) landed what is below; the
listeners, routes, sessions and the Entrance Reducer are later steps of phase 10.

## Layout

| Package | What it holds |
|---|---|
| `errors.py` | `EntranceError` and every refusal, each also in its `hivemind.common.errors` category. |
| `auth/` | Credential primitives: the signed strings (`canonical`), device keys, Argon2id passwords, the wrapped console key, WebAuthn, the `ChallengeBook` ceremonies are answered against, and `SoftPasskey`. |
| `enrol/` | Device enrolment: the enrolled-device model and its state machine, the operator/console bootstrap, invites, redemption by Ed25519 key or passkey, approval and denial, revocation, lock, unlock and the expiry sweep, the device-ceiling and steward rules, and the seams later steps implement. |
| `store/` | The Entrance tables: `EntranceStore`, its SQLite and in-memory implementations; every status change is written with its `guard.entrance_*` trail event in one transaction. |
| `push/`, `routes/` | Skeletons, populated by roadmap steps 10.5 and 10.5b. |

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
- `EntranceStore`, `SqliteEntranceStore`, `MemoryEntranceStore`: the Entrance tables.

Nothing in the Entrance stores a password, an invite code or a private key in the clear: the
tables hold an Argon2id hash and public keys, invites are stored as the SHA-256 of their code,
and the console's private key sits in the secret store sealed under the operator password. No
trail event carries a code, a key, a signature or a password either; a test walks a whole
enrolment lifecycle and checks every event for each of them.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance \
    packages/hivemind/tests/contracts/test_entrance_store_contract.py
```

The password, wrapping and console tests run the real 64 MiB Argon2id (about a tenth of a second
per derivation), so they take a few seconds. `tests/unit/entrance/enrol/test_flow.py` runs the
whole lifecycle (console bootstrap, a program and a browser enrolling, approval, lock, unlock,
revocation, expiry, a refused replay) over in-memory tables and over one real SQLite file. Passkey tests need no browser: `SoftPasskey` answers
the Entrance's options exactly as a browser's `PublicKeyCredential.toJSON()` would, and the real
`webauthn` verification checks it.
