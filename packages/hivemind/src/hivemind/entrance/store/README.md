# hivemind.entrance.store

The Entrance tables: the operator's password hash, the enrolled devices and their invites
(codingrules Appendix C: password hash and public keys only). They live in the Hive's own
`[hive] db` SQLite file under their own migration series (subsystem `entrance`), so they move with
every other store on Supersedure.

| Module | What it holds |
|---|---|
| `protocol.py` | `EntranceStore`, `DeviceChanges`, and the three rules both stores apply inside their own atomic step: `check_new_device` (a device enters only as INVITED, or APPROVED for the loopback-bound console), `transition_device` (the only way a status moves: an edge of the state machine, from the status the caller expected), `use_invite` (an invite admits once, before it expires). |
| `sqlite.py` | `SqliteEntranceStore`: one connection thread and one lock; every write is one `BEGIN IMMEDIATE` transaction that re-reads the row it guards. |
| `memory.py` | `MemoryEntranceStore`: the same contract in dicts, for tests and demos. |
| `migrations/` | `0001_create_entrance.sql` (`entrance_operator`, `entrance_devices`, `entrance_invites`). Later steps add 0002 (sessions), 0003 (push subscriptions) and 0004 (goals by device). |

Every transition is a `guard.entrance_*` trail event in the same transaction as the state change
(Appendix C rule 3); `hivemind.pheromone.GuardEvent` declares the kinds. This data half of roadmap
10.5d records the state change only; the behaviour half adds the event write in that transaction.

## Public API

`EntranceStore`, `DeviceChanges`, `check_new_device`, `transition_device`, `use_invite`;
`SqliteEntranceStore`, `apply_entrance_migrations`, `SUBSYSTEM`, `MIGRATIONS_PACKAGE`;
`MemoryEntranceStore`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/contracts/test_entrance_store_contract.py \
    packages/hivemind/tests/unit/entrance/store
```

The contract suite runs every clause over both stores, including every allowed and every
forbidden status edge through `update_device_status`; `test_sqlite.py` covers the migration
series and durability across a new connection.
