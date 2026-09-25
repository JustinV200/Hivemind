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
  hands to `BackendRegistry.register`. Given a control network (`DockerBackendConfig.control`,
  from `[virtual_cells] control_subnet`) it dual-homes each Cell and can cut and restore a running
  Cell's egress without dropping its link (roadmap step 10.6a; `backends/README.md`).
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
- `CheckStatus` / `CheckResult` / `Attestation` / `CHECK_NAMES` / `attest` / `NightVeilProbe` /
  `FakeNightVeilProbe` / `SessionProbeConfig` / `SessionNightVeilProbe` / `run_checks` /
  `attest_cell` (`night_veil/`, roadmap step 5.7b): deterministic Night Veil bootstrap attestation
  before `CellReady` -- one `NightVeilProbe` check per codingrules 8.7 requirement, `attest` the
  pure all-or-nothing judgement (a documented `NOT_APPLICABLE` for `webrtc_leak_blocked` until
  browser automation exists, never a silent downgrade), `attest_cell` the effectful edge that runs
  every check and records exactly one `cell.attested` trail event, win or lose. `FakeNightVeilProbe`
  is what a composition root uses until `images/night-veil-ubuntu` (5.3a) is attestable for real;
  `SessionNightVeilProbe` runs each check as a fixed, documented command over a `CellSession`. The
  hook to gate `CellReady` on this before `mark_ready` (and tear the Cell down on a red result) is
  a report item: it sits in `hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider.
  acquire`, outside this dispatch's file list.
- `NightVeilBoundary` / `TIER_LABEL` / `with_tier_label` / `tier_from_labels` /
  `provisioned_facts` / `failure_facts` / `end_night_veil` / `adopt_night_veil` /
  `night_veil_cells` / `sweep_night_veil` (`night_veil/boundary.py`, codingrules section 12):
  where the Virtual Cell lifecycle meets the Night Veil retention boundary
  (`hivemind.pheromone.retention`). `CellLifecycle.attach_night_veil` hands the lifecycle the
  boundary the composition root built; from then on a Night Veil Cell's ephemeral segment opens
  as provisioning begins, before the lifecycle records a word about it (a provision its backend
  fails before any Cell exists leaves nothing on view: its `cell.provision_failed` is withheld,
  the skeleton having no such kind, and its segment is dropped unread), and
  `NightVeilTeardownPurge` runs
  every time one ends: in
  `teardown` (a finished task, a provision that failed after the Cell existed, a Hive shutdown),
  in `hive cells abscond` (`adopt_night_veil` says which Cells), and in `reconcile`, whose
  `sweep_night_veil` holds again the segment of a Night Veil Cell that outlived a Queen restart
  and records `cell.destroyed` (when missing) and purges every one the skeleton names that is gone
  unpurged. Every provisioned Cell carries its tier in its backend labels (`TIER_LABEL`), and
  `cell.provisioned` names it, so a restarted Queen reads it back from either.

## Night Veil: what a Cell itself keeps

The boundary above covers the Queen's side. A Night Veil Cell must keep nothing either, so the
backend's destroy has to take everything the Cell wrote with it:

- The in-Cell Warden's trail is a `MemoryPheromoneTrail` (`hivemind.cli.in_cell.main`), never a
  file: it dies with the Warden's process, whatever the backend.
- Docker: `destroy` removes the container, its scratch volume and its network. A Night Veil
  container is created with the `none` log driver (`ContainerSpec.log_driver`), so its stdout
  and stderr reach no daemon log at all, whatever driver the daemon defaults to (`journald`,
  `syslog` or a remote one would keep them on the host). A Capping snapshot
  (`hivemind.hive.snapshot.docker`, a `docker commit`) is an image outside the container,
  labelled `hivemind.snapshot_of=<cell_id>`: a Night Veil teardown removes every one of the
  Cell's (`DockerSnapshotImages`, a side channel of the purge, found by that label through
  `DockerClientPort.list_images`, from any process). Any other Cell's committed images still
  outlive it (`teardown` deletes only their ledger rows), a leak outside the Night Veil boundary.
  The container's writable layer and scratch volume are deleted, not wiped: their blocks stay on
  the host disk until reused.
- QEMU: the backend refuses a Night Veil Cell, fail-closed (`capabilities.can_night_veil` is
  False, so placement never chooses it for the tier, and `provision` refuses such a spec before
  anything exists); no roadmap step promises Night Veil on QEMU. Everything a VM writes lives
  under its `vm_dir`: `overlay.qcow2` (its whole disk, every `savevm` snapshot included),
  `seed.iso` with `user-data` and `meta-data` (its bootstrap, the hidden-service address and its
  private signing key among them), `serial.log` (its console), `qmp.sock` and `cell.json`;
  `destroy` removes that directory. For Night Veil that is not enough, and lifting the refusal
  needs four things not built yet: the removal is
  `shutil.rmtree(..., ignore_errors=True)`, so a failed delete is silent, and it must be verified
  and fail loudly instead; a deleted overlay's blocks stay on the host disk, so the Cell's
  `vm_dir` belongs on a RAM-backed filesystem (or its overlay encrypted under a key held only in
  the Queen's memory, so dropping the key destroys it); the guest's journald must run with
  `Storage=volatile` in `images/night-veil-ubuntu`, so its logs never reach the overlay at all;
  and the image must send none of its own logs to the serial console, since `serial.log` is a
  host file.

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
modules' own docstrings for the split). It mints a Virtual Cell's id itself as provisioning begins
(`VirtualCellSpec.cell_id`, which every backend gives the Cell) and records `cell.provisioning`
under it before any backend is called; `cell.provisioned` (or `cell.provision_failed`) follows
under the same id.
