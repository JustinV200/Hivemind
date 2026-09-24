# hivemind.entrance.store

The Entrance tables: the operator's password hash, the enrolled devices and their invites
(codingrules Appendix C: password hash and public keys only). They live in the Hive's own
`[hive] db` SQLite file under their own migration series (subsystem `entrance`), so they move with
every other store on Supersedure.

| Module | What it holds |
|---|---|
| `protocol.py` | `EntranceStore`, `DeviceChanges`, and the rules both stores apply inside their own atomic step: `check_new_device` (a device enters only as INVITED, or APPROVED for the loopback-bound console, with that entry's event), `check_status_change` and `check_device_event` (a change carries exactly its edge's event, about its device), `transition_device` (the only way a status moves: an edge of the state machine, from the status the caller expected), `use_invite` (an invite admits once, before it expires). |
| `sqlite.py` | `SqliteEntranceStore`: one connection thread and one lock; every write is one `BEGIN IMMEDIATE` transaction that re-reads the row it guards and writes the change's event with `hivemind.pheromone.insert_event`. `create` refuses a file without the trail's table. |
| `memory.py` | `MemoryEntranceStore(trail)`: the same contract in dicts, recording each event on `trail` before the change is applied, for tests and demos. |
| `migrations/` | `0001_create_entrance.sql` (`entrance_operator`, `entrance_devices`, `entrance_invites`). Later steps add 0002 (sessions), 0003 (push subscriptions) and 0004 (goals by device). |

Every entry and every transition is a `guard.entrance_*` trail event written in the same
transaction as the state change (codingrules Appendix C): `put_device(device, event)`,
`update_device_status(device_id, expected, new, event, **changes)` and
`redeem_invite(code_hash, used_at, event, **changes)`, which spends the invite and moves its
device INVITED to PENDING in one step, so neither can happen without the other or without its
event. `hivemind.pheromone.GuardEvent` declares the kinds.

## Public API

`EntranceStore`, `DeviceChanges`, `check_new_device`, `check_status_change`,
`check_device_event`, `transition_device`, `use_invite`;
`SqliteEntranceStore`, `apply_entrance_migrations`, `SUBSYSTEM`, `MIGRATIONS_PACKAGE`;
`MemoryEntranceStore`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/contracts/test_entrance_store_contract.py \
    packages/hivemind/tests/unit/entrance/store
```

The contract suite runs every clause over both stores, including every allowed and every
forbidden status edge through `update_device_status`, each edge's event landing on the trail, and
a refused change (or an event that cannot be recorded) leaving neither the row nor the trail
touched; `test_sqlite.py` covers the migration series, the refusal of a file without the trail's
table, and durability across a new connection.
