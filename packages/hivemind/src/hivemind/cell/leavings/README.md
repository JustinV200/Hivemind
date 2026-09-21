# hivemind.cell.leavings

The Leavings ledger (roadmap steps 5.0a-5.0e): what a task may leave behind on a Cell after its
lease is released. Scratch is still removed wholesale on release and a Cell is still left as
found, *plus exactly the paths this ledger lists* (roadmap phase 5 preamble). This package owns
the ledger's own value types and its persistence; the decision of *whether* to persist a path is
a later step's job (5.0c's policy, 5.0d's `keep` tool) -- `RestoreRecord.persist`/`approved_by`/
`reason` (`hivemind.cell.lease`) only carry that decision through to `release()`.

## Public API (roadmap step 5.0a)

- **Model** (`model.py`): `ApprovedBy` (`POLICY | HUMAN`: who allowed a path to stay), `Leaving`
  (cell id, path, sha256, size, task id, lease id, `approved_by`, the reason, `prior` -- the bytes
  `hive cells leavings remove` replays, never displayed by `list` -- and the left/removed times).
- **Store protocol** (`store_protocol.py`): `LeavingsStore` (`record_leaving`, `get_leaving`,
  `list_leavings`, `mark_removed`), mirroring `hivemind.brood_chamber.store.protocol.TaskStore`'s
  own shape: a row and its Pheromone Trail event commit together, or neither commits at all.
  `record_leaving` upserts: writing a path that already has an active row replaces every field but
  `prior`, which the existing row's own value always wins, so the ledger always holds the bytes
  the path held before any Leaving ever existed there, however many times the same path is left
  again -- one lease noting it twice, or the same goal run twice while an earlier run's row is
  still active -- before it is ever removed. `check_leaving_event` is the guard both
  implementations call first.
- **Memory store** (`store_memory.py`): `InMemoryLeavingsStore`, for tests, demos and a caller
  that never leases (`hive cells list`'s own throwaway use).
- **SQLite store** (`store_sqlite.py`, `migrations/`): `SqliteLeavingsStore`, the durable
  implementation, one `cell_leavings` table keyed by `(cell_id, path)`.

## Who writes and reads this ledger

- `hivemind.cell.local.releaser.HiveStandLeaseReleaser.release` writes: restore records are
  grouped by resolved path first, so a path noted more than once in one lease (persisted twice,
  or a non-persisted write followed, in either order, by a persisted one) still produces exactly
  one outcome. For a path with any `persist=True` record, it reads the path's final content
  (sha256, size) once, builds one `Leaving` (its `prior` from that path's *earliest* record), and
  calls `record_leaving` -- atomic with a `cell.left` trail event. `LeaseReleaseReport.left_paths`
  names exactly those paths; `is_restored` stays true, since leaving them is not a failure. A
  persisted path gone by release time is restored to its earliest `prior` like any other path,
  unless that `prior` is itself `None` (nothing was there before this lease either), in which case
  it is neither residual nor a Leaving -- the Cell already looks exactly as found.
- `hive cells leavings list|remove <cell>` (`hivemind.cli.readback.leavings`) reads and clears
  it: `list` prints every active row (or every row, with `--include-removed`), never `prior`;
  `remove` replays `prior` back (or unlinks, when it was `None`) onto the real filesystem, then
  `mark_removed` -- atomic with a `cell.leaving_removed` event.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/cell/leavings packages/hivemind/tests/contracts/test_leavings_store_contract.py
```

Coverage floor is 95% for `hivemind.cell` as a whole (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.cell.leavings uv run --frozen pytest -p no:cacheprovider --cov=hivemind.cell.leavings \
    --cov-report=term-missing packages/hivemind/tests/unit/cell/leavings packages/hivemind/tests/contracts/test_leavings_store_contract.py
```
