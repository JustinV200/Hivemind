# hivemind.hive

The hive package (lowercase, distinct from the Hive as a whole) provisions and destroys Virtual
Cells: VM or container Cells the Queen owns outright rather than borrows. It covers their
lifecycle, Night Veil attestation (the always-teardown-only security tier) and the Overwintering
pool that keeps a dormant Cell around for fast reuse.

## Public API (roadmap step 5.1/5.2)

- `VirtualCellSpec` / `NetworkPolicy` (`models.py`): a request to provision one Virtual Cell --
  image, resources, lifetime, network policy, Exoskeleton flag, the `ForageCapacity` the image
  promises, its `CombShieldLevel`, a ready timeout, its `hive_id` and free-form labels.
- `VirtualCellStatus` / `TRANSITIONS` / `can_transition` / `assert_transition` /
  `can_enter_dormant` / `assert_dormant_allowed` (`cell_state.py`): the one state machine every
  Virtual Cell moves through, `PROVISIONING -> READY -> GRANTED -> RELEASED -> (DORMANT |
  DESTROYING) -> DESTROYED`, plus the separate Night Veil invariant (never DORMANT).
- `CellBackend` / `BackendCapabilities` / `VirtualCellRecord` (`backends/base.py`): the protocol
  every provisioning backend implements, its declared capabilities, and what `list_cells` returns.
- `FakeCellBackend` (`backends/fake.py`): the in-memory reference implementation, used by tests,
  demos and `hive doctor`.
- `CellBootstrap` / `QueenEndpoint` / `ReadinessGate` / `CellReadyInfo` / `mint_cell_bootstrap`
  (`backends/bootstrap.py`): the backend-independent identity and readiness seam every
  `CellBackend` provisions through (roadmap step 5.4); `FakeReadinessGate` (`backends/fake.py`)
  is its in-memory implementation.
- `DockerCellBackend` / `build_docker_backend` (`backends/docker/`): the first working
  `CellBackend`, over a Docker daemon (ADR-0026, ADR-0027), and the factory a composition root
  hands to `BackendRegistry.register`.
- `QemuCellBackend` / `build_qemu_backend` (`backends/qemu/`): the second `CellBackend`, over real
  QEMU VMs booted from a prebuilt qcow2 and cloud-init (roadmap step 5.11); same contract, gives
  `isolation = "required"` a real hypervisor boundary. `hivemind.hive.backends.cloud` holds the
  (optional, post-1.0) cloud provider seam, reached directly rather than re-exported here.
- `BackendRegistry` / `CellBackendFactory` (`registry.py`): name -> `CellBackend`, for the
  composition root.
- `HiveError` and its subclasses (`errors.py`): this package's own error tree.
- `DockerSnapshotter` / `QemuSnapshotter` / `SnapshotLedger` / `SnapshotRecord` /
  `SnapshotNotFoundError` / `snapshotter_for` (`snapshot/`): Snapshotter implementations for
  Virtual Cells (roadmap step 5.10) -- a `docker commit`/recreate rollback and a QMP
  `savevm`/`loadvm` rollback, both accounted in one shared, in-memory `SnapshotLedger` (retention
  expiry, budget eviction). `snapshotter_for` picks the right one for a registered `CellBackend`
  by `capabilities.can_snapshot` alone, never by name; a backend that cannot snapshot gets
  `hivemind.cell.NoopSnapshotter` instead, so `hivemind.supervision.capping.gate.CappingGate`
  falls back to REVERSE_DIFF (ADR-0018) with no branch of its own.

## How to test this

- `packages/hivemind/tests/unit/hive/`: unit tests for every module above, mirroring `src/`.
- `packages/hivemind/tests/contracts/test_cell_backend_contract.py`: one contract suite run over
  every `CellBackend` implementation -- `FakeCellBackend`, `DockerCellBackend` (over
  `FakeDockerClient` and `FakeReadinessGate`), `QemuCellBackend` (over `FakeQemuRunner` and
  `FakeReadinessGate`), and `FakeCloudCellBackend`.
- `packages/hivemind/tests/integration/test_docker_backend.py`: `@pytest.mark.integration`,
  provisions a real container against a loopback Waggle server; skips cleanly with no Docker
  daemon reachable.
- `packages/hivemind/tests/integration/test_qemu_backend.py`: `@pytest.mark.integration`,
  provisions a real VM the same way; skips cleanly with no `qemu-system-x86_64`/`qemu-img` on PATH.

## Not yet built (later roadmap steps)

- `night_veil.py` (5.7b).
- A real `hivemind.hive.backends.cloud` provider implementation (post-1.0; see that package's own
  README for what it must add).

`snapshot/` (5.10) is built: `DockerSnapshotter`, `QemuSnapshotter`, `SnapshotLedger` and
`snapshotter_for`. Wiring a Snapshotter into a Warden's own `GateDeps.snapshotter` is not built by
this dispatch (see `hivemind.wardens.spawn.spawn._build_capping_gate`, which still hardcodes
`NoopSnapshotter()`, and `hivemind.cli.in_cell.deps`, which builds no Snapshotter at all -- both
are outside this dispatch's file list; see its own report for the exact lines and the cross-Cell
relay gap this uncovers for the in-Cell Warden case).

`lifecycle.py` (5.6) is built: `CellLifecycle` is the only intended caller of
`cell_state.assert_transition`/`assert_dormant_allowed`, and now also owns every `CellBackend` call
and every `overwinter/` (5.9) edge (`OverwinterPool` is bookkeeping and selection only; see both
modules' own docstrings for the split).
