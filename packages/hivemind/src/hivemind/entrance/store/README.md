# hivemind.entrance.store

The Entrance tables: the operator's password hash, the enrolled devices and their invites
(codingrules Appendix C: password hash and public keys only), and, from roadmap step 10.5e, the
tables login, sessions, step-up and the Entrance Reducer keep. They live in the Hive's own
`[hive] db` SQLite file under their own migration series (subsystem `entrance`), so they move with
every other store on Supersedure.

| Module | What it holds |
|---|---|
| `protocol.py` | `EntranceStore` (which extends `AuthTables`), `DeviceChanges`, and the rules both stores apply inside their own atomic step: `check_new_device`, `check_status_change`, `check_device_event`, `transition_device` (the only way a status moves), `use_invite` (an invite admits once), `apply_login` (a login recorded on an APPROVED device; a passkey's counter only moves forward). |
| `tables.py` | `AuthTables`: the four 10.5e tables as properties (`sessions`, `logins`, `pending`, `entrance_mode`). |
| `sqlite.py` | `SqliteEntranceStore`: one connection thread and one lock; every write is one `BEGIN IMMEDIATE` transaction that re-reads the row it guards and writes the change's event with `hivemind.pheromone.insert_event`. `create` refuses a file without the trail's table. |
| `memory.py` | `MemoryEntranceStore(trail)`: the same contract in dicts. |
| `link.py` | `SqliteLink`: the connection, thread and lock every SQLite table shares. |
| `sessions/` | `SessionTable`: sessions by token hash and spent nonces (persisted, purged past twice the skew); SQLite and in-memory forms, and `SplitSessionTable`, which keeps the Hive Stand console's sessions in memory (the durable table refuses one). |
| `logins/` | `LoginTable`: each device's consecutive login failures and the networks it has been cleared on. |
| `pending/` | `PendingTable`: requests held until a person confirms them, settled once along an edge of `hivemind.entrance.auth.confirm.state`. |
| `mode/` | `ModeTable`: the Entrance mode (`OPEN`/`REDUCED`), one row, each change written with its `guard.reduced` or `guard.reopened` event. |
| `migrations/` | `0001_create_entrance.sql` (operator, devices, invites) and `0002_create_sessions.sql` (sessions, nonces, login failures, device networks, pending confirmations, the mode). |

Every entry and every transition is a `guard.entrance_*` trail event written in the same
transaction as the state change (codingrules Appendix C), and so is every mode change. Rows of
0002 name an enrolled device (foreign keys); the in-memory tables apply the same rule. Sessions
are stored as plain columns (a request touches one on every call); the other rows follow 0001's
shape, filter columns plus the record's JSON body.

## Public API

`EntranceStore`, `DeviceChanges`, `AuthTables`, `check_new_device`, `check_status_change`,
`check_device_event`, `transition_device`, `use_invite`, `apply_login`; `SqliteEntranceStore`,
`apply_entrance_migrations`, `SUBSYSTEM`, `MIGRATIONS_PACKAGE`; `MemoryEntranceStore`;
`SessionTable`, `SqliteSessionTable`, `MemorySessionTable`, `SplitSessionTable`; `LoginTable`,
`SqliteLoginTable`, `MemoryLoginTable`; `PendingTable`, `SqlitePendingTable`,
`MemoryPendingTable`; `ModeTable`, `SqliteModeTable`, `MemoryModeTable`; `SqliteLink`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/contracts/test_entrance_store_contract.py \
    packages/hivemind/tests/contracts/test_entrance_session_table_contract.py \
    packages/hivemind/tests/contracts/test_entrance_auth_tables_contract.py \
    packages/hivemind/tests/unit/entrance/store
```

The contract suites run every clause over both stores (the session suite over the split table
too), including every allowed and forbidden status edge, a refused change leaving neither the row
nor the trail touched, a nonce granted once until it expires, and a pending confirmation settled
once; `test_sqlite.py` covers the migration series, the refusal of a file without the trail's
table, and durability of every table across a new connection.
