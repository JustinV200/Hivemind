# hivemind.queen.requeening

The requeening package is Requeening: restoring a crashed Queen along with her outstanding
leases and Forage grants, so no task is silently orphaned (roadmap 13.1), and the Queen's own
periodic snapshot that keeps that recovery bounded (roadmap 13.2). The decision is ADR-0062
(`docs/adr/0062-requeening-recovery-sources-of-truth.md`); the operator's side is
`docs/runbooks/requeening.md`.

Every start of a Queen is a Requeening. Each fact comes back from its own source of truth:

| Fact | Source | Module |
|---|---|---|
| Tasks and their placement | Brood Chamber | `recover` (sorting), `orphans` (taking back) |
| Virtual Cells and their keys | Backend labels | `hivemind.queen.cell_gate.rearm` (before her loop) |
| Hive Stand leases | Trail, folded from the newest snapshot | `leases`, `snapshot` |
| Grants | Forage ledger | `recover`, `orphans` (`revoke_held_by`) |
| Wardens | Whatever reattaches within `requeening_grace_s` | `recover` (`settle_requeening`) |
| Handled Alarms and questions | `queen_handled` table | `handled`, `store` |

```text
run_hive._open - rearm_gate - begin_requeening -+- restore_handled
                                                +- release_orphans (Hive Stand leases)
                                                +- take_back (orphans) + revoke_held_by
                                                +- pending: expected Wardens, deadline
Queen tick - run_housekeeping -+- settle_requeening: take back the missing, resume pauses,
                               |  queen.requeened, REQUEENING -> RUNNING
                               +- take_snapshot_when_due (every snapshot_interval_s)
Queen tick - _handle_item - is_replay (drop) ... act ... note_handled
```

## Public API

- **Recovery** (`recover`): `begin_requeening(queen, RequeeningStart)`,
  `settle_requeening(deps, wardens)`, `REQUEENED_KIND`.
- **State** (`book`): `RequeeningBook` (on `QueenDeps.requeening`), `PendingRecovery`,
  `RequeenCounts`, the two cadence defaults.
- **Handled envelopes** (`handled`): `is_replay`, `note_handled`, `note_question`,
  `restore_handled` (and `prune_handled`, which each snapshot calls: a question's wiring is kept
  until the question closes, however long that takes).
- **Snapshot** (`snapshot`): `take_snapshot_when_due`, `take_snapshot`, `current_leases`.
- **Leases** (`leases`): `HiveStandLeases`, `fold_leases`, `release_orphans`.
- **Orphans** (`orphans`): `take_back`, `clustering_paused`, `revoke_held_by`, `newest_handoff`.
- **Rows and store** (`models`, `store/`): `QueenSnapshot`, `OpenLease`, `HandledEnvelope`,
  `QuestionWiring`; `RequeeningStore`, `MemoryRequeeningStore`, `SqliteRequeeningStore`.

Nothing in this package imports `hivemind.queen.deps`, `.dispatcher` or `.swarm_gate` at module
level: `QueenDeps` holds a `RequeeningBook`, so those are imported where they are used.

## How to test this

`tests/unit/queen/requeening/` covers the store contract (in memory and SQLite), the lease fold
and who gets a lease back, the handled table through two Queens over one store (an Alarm resent
after a restart is dropped; a question asked before it is still answered), taking back each kind
of orphan, the two halves of recovery, and the snapshot cadence and bound.
`tests/e2e/test_requeening.py` kills a Queen with a task on the Hive Stand and one on an
in-process Virtual Cell, and checks both finish under a second Queen.
