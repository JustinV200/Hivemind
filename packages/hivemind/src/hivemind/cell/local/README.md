# hivemind.cell.local

The local package is the Hive Stand: the machine the Queen herself runs on, which doubles as the
first Real Cell and the default home of every Warden (the per-Cell supervisor). It needs nothing
beyond the standard library.

## Public API (roadmap step 3.11)

- **Config** (`hivemind.cell.local.config`): `HiveStandConfig` -- the Hive Stand's own settings
  (`enabled`, `scratch_root`, `scratch_quota_mb`, `disk_reserve_mb`, the `cores`/`memory_bytes`/
  `max_sub_bees` probe overrides, `access_level`, `comb_shield`), resolved once from the
  manifest's `[hive_stand]` section by `HiveStandConfig.from_section(section, manifest_dir)`.
- **Probe** (`hivemind.cell.local.probe`): `probe_host(config) -> ProbeResult` -- this host's
  `CellCapabilities` and `ForageCapacity`, POSIX and Windows shims side by side, best effort
  everywhere except zero usable cores (`ProbeError`); `refresh_live(config, capacity)` -- recomputes
  only the figures that change moment to moment (free memory, free disk, load) between calls.
- **Quota** (`hivemind.cell.local.quota`): `ScratchQuota` (the byte cap plus a `sizer:
  DirectorySizer`, real by default), `QUOTA_SAMPLE_INTERVAL_S` (the watchdog's tick, 0.25s) and
  `directory_size_bytes(root)` (a symlink-safe recursive size walk, `ScratchQuota`'s default
  sizer). A test injects its own `DirectorySizer` into `ScratchQuota.sizer` instead of racing a
  real subprocess's write speed against the watchdog's poll interval -- real cross-process
  file-size polling is not reliable everywhere while a write is in flight (`test_quota.py`'s own
  nested-archive test drives its quota crossing this way).
- **Session** (`hivemind.cell.local.session`): `LocalProcessSession` -- a `CellSession` over a
  real `asyncio.create_subprocess_exec` child, one process group per command, a watchdog that
  kills the whole tree on a timeout or a scratch-quota breach, and on POSIX an `RLIMIT_FSIZE`
  set via `preexec_fn` as a second line of defence between watchdog samples.
- **Releaser** (`hivemind.cell.local.releaser`): `HiveStandLeaseReleaser` -- kills every process a
  lease started, then groups its `RestoreRecord`s by resolved path (so a path noted more than
  once this lease, persisted or not, resolves to exactly one outcome) and either restores each
  path to its group's earliest `prior`, or, for a path with any `persist=True` record (roadmap
  step 5.0a), reads its current content back and writes it as a `Leaving`
  (`hivemind.cell.leavings.LeavingsStore.record_leaving`, atomic with a `cell.left` trail event)
  instead, landing in `LeaseReleaseReport.left_paths`; a persisted path gone by release time falls
  back to an ordinary restore of its earliest `prior`, unless that `prior` was `None` too (already
  as found). Then removes the lease's scratch directory wholesale. `kill_process_tree(pid,
  clock)` -- the shared "kill this process group, POSIX SIGTERM-then-SIGKILL or Windows `taskkill
  /T /F`" logic this releaser and the session's own watchdog/`close()` both use. Takes a
  `LeavingsStore` and a `CellIdentity` at construction (the same identity its lease was built
  with).
- **Source** (`hivemind.cell.local.source`): `HiveStandSource` -- the `RealCellSource` for the
  Hive Stand's one Cell: `cells()` always returns that one Cell with live figures refreshed;
  `lease()` refuses while disabled, already leased, over the requested access level, or under the
  configured disk reserve, otherwise creating `<scratch_root>/<lease_id>/` and opening a
  `RealCellLease`; `open_session()` hands back a `LocalProcessSession` sized from
  `scratch_quota_mb`. Takes a `LeavingsStore` at construction (roadmap step 5.0a) and hands it
  straight to every `HiveStandLeaseReleaser` it builds. `HIVE_STAND_SOURCE` (`"hive_stand"`, the
  `source` field every Cell this source hands out carries) and `hive_stand_cell_id(node_id) ->
  CellId` (a pure function: the `cell_` prefix plus `node_id`'s own 26-char ULID, validated with
  `waggle.ids.parse_id`) live beside it -- `HiveStandSource.__init__` calls the latter instead of
  minting a fresh id, so the Hive Stand's one Cell keeps the same id across every `hive run` on
  this node (phase 7 handoff item 4: a fresh id every run used to strand `cell:<id>` Honey, Cell
  Wax history and Leavings from the process that wrote them). Both names are also re-exported
  from `hivemind.cell.local` and `hivemind.cell` itself.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/cell/local packages/hivemind/tests/contracts/test_cell_session_contract.py packages/hivemind/tests/contracts/test_real_cell_source_contract.py
```

`test_left_as_found.py` and `test_quota.py` spawn real `sys.executable` subprocesses and take real
wall-clock time (a few seconds); they are ordinary unit tests here, not integration tests, and are
written to hold on both Windows and Ubuntu.

Coverage floor is 95% for `hivemind.cell` as a whole (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.cell.local uv run --frozen pytest -p no:cacheprovider --cov=hivemind.cell.local \
    --cov-report=term-missing packages/hivemind/tests/unit/cell/local
```
