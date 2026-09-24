# hivemind.cell

The cell package defines the Cell abstraction shared by every kind of machine the Hive runs work
on: Cell, CellKind (REAL for a borrowed device or VIRTUAL for a provisioned one), CellCapabilities
(platform facts plus capability flags), CellSession (a terminal session on a Cell), a Real Cell's
lease and its state machine, RealCellSource (how Real Cells are inventoried and leased),
Snapshotter, TaskNeeds and the three security tier enums (AccessLevel, CombShieldLevel,
HoneyClearance). It knows what a Cell is and how a Real Cell's lease works, never how a Virtual
Cell is made (`hive`'s job) or how a Real Cell is produced beyond the Hive Stand (`cell.local`
here; `swarm` for enrolled devices, a later phase).

## Public API (roadmap steps 3.10, 3.11)

- **Tiers** (`hivemind.cell.tiers`): `AccessLevel` (`READ_ONLY`, `SCRATCH`, `FULL`, with a
  `rank` property), `CombShieldLevel` (`MEADOW`, `PROPOLIS`, `NIGHT_VEIL`) and `HoneyClearance`
  (`C0`, `C1`, `C2`, with a `rank` property).
- **Needs** (`hivemind.cell.needs`): `Isolation`, `OsFamily` and `TaskNeeds`.
- **Models** (`hivemind.cell.models`): `CellKind` (`REAL`, `VIRTUAL`); `CellCapabilities`
  (platform facts -- `os`, `arch`, `distribution`, `shell`, `package_manager`, `python_version`
  -- plus capability flags -- `has_display`, `has_audio`, `has_browser`, `can_start_display`,
  `can_host_model`, `real_display_allowed` (roadmap step 6.3: the operator's own opt-in to let the
  Hive drive the display already running there), `network_scopes` -- with `to_wire()`/`from_wire()`
  converting to and from the
  waggle `PlatformReport`/`CellCapabilitiesReport` pair); `Cell` (id, kind, name, source,
  capabilities, capacity, access_level, comb_shield), whose validator refuses a REAL Cell at
  `NIGHT_VEIL` and a VIRTUAL Cell below `FULL` access.
- **Session** (`hivemind.cell.session`): `CellSession` (the terminal Protocol: `exec`, `put_file`,
  `delete_file`, `get_file`, `scratch_dir`, `is_open`, `close`, and since roadmap step 6.4
  (ADR-0031) `start`/`stop`/`is_running` for long-running background processes: a display, a sound
  server, a browser, later a model server), `ExecSpec`, `ExecEvent` (`OutputChunk | ExitStatus`),
  `BackgroundSpec`/`BackgroundProcess` (what `start` takes and returns), `CompletedCommand`,
  `run(session, spec)` (drives `exec` to completion), and `resolve_scratch_path` (the shared
  relative-path-under-scratch, `..`/symlink-safe resolution every concrete session's
  `put_file`/`get_file` uses).
- **Lease** (`hivemind.cell.lease_state`, `hivemind.cell.lease`): `LeaseState`
  (`REQUESTED -> OPEN -> RELEASING -> RELEASED`; `OPEN -> ORPHANED -> RELEASING`; `RELEASING ->
  ORPHANED -> RELEASING`, a release() whose delegate raised, retried) with
  `can_transition`/`assert_transition`; `LeaseRequest`, `LeaseFacts`, `LeaseReleaseReport` (now
  also `left_paths`, roadmap step 5.0a), `RestoreRecord` (one path written outside scratch, and
  what `release()` must put back -- now also `persist`/`approved_by`/`reason`, validated
  together), `LeaseReleaser` (the injected Protocol `release()` delegates to) and `RealCellLease`
  -- `open()` and `release()` each write their own trail event (`cell.leased`, `cell.released`) in
  the same call that changes `state`; `release()` is idempotent on success, and moves a lease to
  ORPHANED (re-raising) rather than leaving it stuck in RELEASING when its delegate raises, so a
  later call -- a retry, or the Undertaker's sweep (roadmap 5.8) -- is legal again;
  `note_started_process`,
  `note_touched_path` (writes `cell.touched_outside_scratch` when the path is outside scratch),
  `note_restore_path`/`restore_records` (sync bookkeeping for what a Capping proposal wrote
  outside scratch, roadmap step 3.17; a path inside scratch is never recorded; roadmap step 5.0a
  grows a `persist`/`approved_by`/`reason` keyword trio so a `persist=True` record can carry what
  the Leavings ledger row `release()` writes needs) and `is_path_allowed` round out the
  bookkeeping a Warden and the Undertaker read.
- **Leavings** (`hivemind.cell.leavings`, roadmap step 5.0a): the ledger of paths a task was
  allowed to keep on a Cell past its lease's release -- `Leaving`, `ApprovedBy`, `LeavingsStore`
  (`record_leaving`, `get_leaving`, `list_leavings`, `list_all_leavings` -- every Cell's active
  Leavings in one call, since every `hive run` mints a fresh Cell id -- `mark_removed`) and its
  two implementations; see `cell/leavings/README.md` for its own public API. Written by
  `hivemind.cell.local.HiveStandLeaseReleaser.release`; read and cleared by `hive cells
  leavings list|remove [CELL] [--path PATH]`.
- **Source** (`hivemind.cell.source`): `RealCellSource` (`name`, `cells()`, `lease(request)`,
  `open_session(lease)`) and `CellIdentity` (the Hive/node/actor a source stamps on its events).
- **Snapshot** (`hivemind.cell.snapshot`): `Snapshotter`, `NoopSnapshotter` (every Real Cell
  source's Snapshotter: `snapshot` always succeeds and warns once per Cell id; `rollback` always
  raises `SnapshotUnsupportedError`).
- **Fake** (`hivemind.cell.fake`): `FakeSession` (in-memory files dict, scripted `exec`) and
  `FakeCellSource`/`FakeLeaseReleaser` (an in-memory `RealCellSource` over a fixed Cell
  inventory; refuses a lease on an unknown or still-open Cell with `LeaseRefusedError`).
- **Errors** (`hivemind.cell.errors`): `CellError` and its tree --
  `LeaseRefusedError`, `SessionClosedError`, `CommandTimeoutError`, `PathNotAllowedError`,
  `SnapshotUnsupportedError`, `InvalidLeaseTransitionError`, `ProbeError`,
  `ScratchQuotaExceededError` (a lease's scratch directory outgrew its quota mid-command),
  `LeavingNotFoundError`, `LeavingAlreadyRemovedError` (roadmap step 5.0a: a
  `hivemind.cell.leavings.LeavingsStore` lookup or a second `remove` found no active row).
- **Local** (`hivemind.cell.local`): the Hive Stand, the machine the Queen runs on and the first
  Real Cell source (roadmap step 3.11) -- `HiveStandConfig`, `probe_host`/`refresh_live`,
  `LocalProcessSession`, `HiveStandLeaseReleaser`, `HiveStandSource`; see `cell/local/README.md`
  for its own public API.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/cell packages/hivemind/tests/contracts/test_cell_session_contract.py packages/hivemind/tests/contracts/test_real_cell_source_contract.py
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.cell uv run --frozen pytest -p no:cacheprovider --cov=hivemind.cell \
    --cov-report=term-missing packages/hivemind/tests/unit/cell
```
