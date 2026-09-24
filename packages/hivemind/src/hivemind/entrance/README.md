# hivemind.entrance

The Hive Entrance is the only door into the Hive: two listeners, loopback (always on) and remote
(only when explicitly exposed), serving the Landing Board (the versioned public API contract),
device enrolment, auth, push delivery, exposure control and the human inbox. Approval routes never
exist on the remote listener. Every client, the Hive Stand's own console included, is a device
enrolled with its own key and approved at the Hive Stand, and logs in with that key plus the
operator's password (codingrules 8.15, `docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md`).

Roadmap step 10.4 and the data half of 10.5d landed the foundation below; the listeners, routes,
sessions, push and the Entrance Reducer are later steps of phase 10.

## Layout

| Package | What it holds |
|---|---|
| `errors.py` | `EntranceError` and every refusal, each also in its `hivemind.common.errors` category. |
| `auth/` | Credential primitives: the signed strings (`canonical`), device keys, Argon2id passwords, the wrapped console key, WebAuthn, and `SoftPasskey`. |
| `enrol/` | The enrolled-device model and its state machine, and the operator/console bootstrap. |
| `store/` | The Entrance tables: `EntranceStore`, its SQLite and in-memory implementations. |
| `push/`, `routes/` | Skeletons, populated by roadmap steps 10.5 and 10.5b. |

## Public API

The face (`hivemind.entrance`) re-exports what most callers need; each sub-package's own face
lists the rest.

- `EntranceError` and its subclasses: every refusal the Entrance makes on purpose.
- `KeyKind`, `PasswordHasher`, `RelyingParty`, `SoftPasskey`: the credential primitives.
- `DeviceStatus`, `EnrolledDevice`, `DeviceDescription`, `DeviceInvite`, `OperatorCredential`:
  the enrolled-device model and its state machine.
- `ConsoleDeps`, `bootstrap_operator`, `change_operator_password`, `unlock_console_key`: the
  operator and the Hive Stand console.
- `EntranceStore`, `SqliteEntranceStore`, `MemoryEntranceStore`: the Entrance tables.

Nothing in the Entrance stores a password, an invite code or a private key in the clear: the
tables hold an Argon2id hash and public keys, invites are stored as the SHA-256 of their code,
and the console's private key sits in the secret store sealed under the operator password.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance \
    packages/hivemind/tests/contracts/test_entrance_store_contract.py
```

The password, wrapping and console tests run the real 64 MiB Argon2id (about a tenth of a second
per derivation), so they take a few seconds. Passkey tests need no browser: `SoftPasskey` answers
the Entrance's options exactly as a browser's `PublicKeyCredential.toJSON()` would, and the real
`webauthn` verification checks it.
