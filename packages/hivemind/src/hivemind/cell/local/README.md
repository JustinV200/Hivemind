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
  lease started, replays its `RestoreRecord`s in reverse (skipping anything the operator approved
  to persist), then removes the lease's scratch directory wholesale; `kill_process_tree(pid,
  clock)` -- the shared "kill this process group, POSIX SIGTERM-then-SIGKILL or Windows `taskkill
  /T /F`" logic this releaser and the session's own watchdog/`close()` both use.
- **Source** (`hivemind.cell.local.source`): `HiveStandSource` -- the `RealCellSource` for the
  Hive Stand's one Cell: `cells()` always returns that one Cell with live figures refreshed;
  `lease()` refuses while disabled, already leased, over the requested access level, or under the
  configured disk reserve, otherwise creating `<scratch_root>/<lease_id>/` and opening a
  `RealCellLease`; `open_session()` hands back a `LocalProcessSession` sized from
  `scratch_quota_mb`.

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
