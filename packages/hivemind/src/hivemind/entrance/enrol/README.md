# hivemind.entrance.enrol

Every client of the Hive Entrance is a device enrolled with its own key and approved at the Hive
Stand (codingrules 8.15, ADR-0033). This package holds the data half of enrolment (roadmap
10.5d): the device record, its state machine, and the bootstrap of the Hive Stand's own console.
The invite, redemption, approval and revocation flows, the `hive entrance` commands and their
`guard.entrance_*` trail events are the behaviour half, built on these.

## The state machine (`state.py`)

```text
INVITED ──► PENDING ──► APPROVED ◄──► LOCKED
   │           │           │            │
   ├─► EXPIRED ├─► DENIED  ├─► EXPIRED  ├─► EXPIRED
   └─► REVOKED └─► EXPIRED └─► REVOKED  └─► REVOKED
```

One table, `TRANSITIONS`, each edge commented and carrying the trail kind it is recorded as
(`guard.entrance_<new status>`, and `guard.entrance_unlocked` for LOCKED to APPROVED); a record is
created with `guard.entrance_invited`. DENIED, EXPIRED and REVOKED are terminal. Approval, denial,
unlock and revocation are loopback-only decisions. A device enters as INVITED; only the
loopback-bound console is created APPROVED.

## The records (`models.py`)

- `EnrolledDevice`: status, key kind and public key (raw Ed25519, or a passkey's COSE key with its
  credential id, sign count, relying party and backup flags), what approval bound (name,
  capabilities as strings, daily spend cap, expiry, interactivity), the console flag, the
  self-description, and when it was created, approved and last seen, and from which network.
  Its `fingerprint` is the grouped key digest every approval surface shows.
- `DeviceDescription`: what a device says about itself when it redeems; display text refused if
  it carries control or bidirectional characters.
- `DeviceInvite`: single use, stored only as the SHA-256 of its code.
- `OperatorCredential`: the one operator row, an Argon2id PHC string (never a password).

## The console (`console.py`)

`bootstrap_operator` checks the password, mints the console's Ed25519 key into the secret store
wrapped under the password, records the console APPROVED, loopback-bound, interactive and
uncapped, and writes the password hash last (its presence is what "initialised" means, so an
interrupted bootstrap is simply run again). `change_operator_password` proves the current password
and re-wraps the console key under the new one; rerunning a change that died midway finishes it.
`unlock_console_key` opens the key for a console login. Console sessions are never persisted.

## Public API

`DeviceStatus`, `TRANSITIONS`, `TERMINAL_STATUSES`, `INVITED_TRAIL_KIND`, `assert_transition`,
`can_transition`, `trail_kind`, `is_terminal`; `EnrolledDevice`, `DeviceDescription`,
`DeviceInvite`, `OperatorCredential`; `ConsoleDeps`, `bootstrap_operator`,
`change_operator_password`, `unlock_console_key`, `CONSOLE_KEY_NAME`, `CONSOLE_CAPABILITIES`,
`CONSOLE_DEVICE_NAME`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/enrol
```

`test_state.py` walks every allowed edge and asserts every forbidden one raises;
`tests/contracts/test_entrance_store_contract.py` does the same through both stores. Builders for
devices in any status live in `packages/hivemind/tests/builders/entrance.py`.
