"""Provision and destroy Virtual Cells: VM or container Cells the Queen owns, not borrows.

This is the hive package (lowercase, distinct from the Hive as a whole). It covers their lifecycle
(roadmap step 5.6, not yet built), Night Veil attestation (the always-teardown-only security tier,
step 5.7b) and the Overwintering pool that keeps a dormant Cell around for fast reuse (step 5.9).
Step 5.1/5.2 land first: the Virtual Cell request and lifecycle types (`VirtualCellSpec`,
`NetworkPolicy`, `VirtualCellStatus`), the `CellBackend` protocol every provisioning backend
implements, its in-memory reference implementation (`FakeCellBackend`), and the registry a
composition root uses to look a named backend up (`BackendRegistry`).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by workers.launch (a later
    phase), and the wardens supervising the Cells it makes. Calls into hivemind.cell,
    hivemind.common and hivemind.forage.

Key invariants:
    - Every Cell produced here has kind == CellKind.VIRTUAL; Real Cells never pass through.
    - A NIGHT_VEIL VirtualCellSpec always carries network_policy=NetworkPolicy.VPN_TOR, and vice
      versa (VirtualCellSpec's own validator enforces this both ways).
    - A Cell whose CombShieldLevel is NIGHT_VEIL may never reach VirtualCellStatus.DORMANT
      (hivemind.hive.cell_state.assert_dormant_allowed enforces this ahead of hivemind.hive.
      lifecycle, which does not exist yet).

See Also:
    - .claude/codingrules.md section 4 for the layer 3 row this package occupies.
    - .claude/codingrules.md Appendix A.1 for the CellBackend shape this package implements.
    - .claude/roadmap.md phase 5 for the work that populates this package, step by step.
    - hivemind.cell.local and hivemind.swarm for the Real Cell sources this package never touches.

Public API:
    - VirtualCellSpec, NetworkPolicy: a request to provision one Virtual Cell; CellReservation:
      what its backend reserves for it, and so its capacity on both sides of its link
      (hivemind.hive.models).
    - VirtualCellStatus, TRANSITIONS, can_transition, assert_transition, can_enter_dormant,
      assert_dormant_allowed: the Virtual Cell lifecycle state machine (hivemind.hive.cell_state).
    - HiveError, CellProvisionError, CellDestroyError, CellEgressError, UnknownBackendError,
      InvalidCellTransitionError, BackendCapabilityError: this package's error tree
      (hivemind.hive.errors).
    - CellBackend, BackendCapabilities, VirtualCellRecord, FakeCellBackend: the provisioning
      protocol, its capability declaration, its list_cells value type, and its in-memory
      reference implementation (hivemind.hive.backends).
    - CellBootstrap, QueenEndpoint, ReadinessGate, CellReadyInfo, mint_cell_bootstrap,
      FakeReadinessGate: the backend-independent identity and readiness seam every CellBackend
      provisions through, and its in-memory implementation (hivemind.hive.backends).
    - DockerCellBackend, build_docker_backend: the first working CellBackend, over a Docker
      daemon, and the factory a composition root hands to BackendRegistry.register
      (hivemind.hive.backends).
    - QemuCellBackend, build_qemu_backend: the second working CellBackend, over real QEMU VMs
      (roadmap step 5.11), and its own registry factory (hivemind.hive.backends).
    - BackendRegistry, CellBackendFactory: name -> CellBackend, for the composition root
      (hivemind.hive.registry).
    - CellLifecycle, LiveVirtualCell, LifecycleDormantCell, LifecycleVirtualBackend,
      OverwinterSettings: the Virtual Cell lifecycle (roadmap step 5.6; hivemind.hive.lifecycle).
      Owns every state edge and every backend call, including the ones OverwinterPool used to make
      itself before this branch's own reconciliation of the two modules. `CellLifecycle`'s
      optional `snapshot_ledger` (roadmap step 5.10) deletes a destroyed Cell's own snapshots on
      teardown.
    - OverwinterDecision, ReleaseOutcome, PoolView, OverwinterConfig, ReleaseDecision,
      decide_release, DormantCell, PooledCandidate, Scrubber, OverwinterPool: the Overwintering
      pool, roadmap step 5.9 (hivemind.hive.overwinter). OverwinterPool is bookkeeping and
      selection only; CellLifecycle calls it, and the backend, around every edge.
    - DockerSnapshotter, QemuSnapshotter, SnapshotLedgerPort, SnapshotLedger, SqliteSnapshotLedger,
      SnapshotRecord, SnapshotNotFoundError, snapshotter_for: Snapshotter implementations for
      Virtual Cells (roadmap step 5.10; hivemind.hive.snapshot). The Warden injects the right one
      into a sub-bee's `hivemind.supervision.capping.gate.GateDeps.snapshotter`, so Capping never
      imports `hive`; `SqliteSnapshotLedger` is the durable `SnapshotLedgerPort` a rollback in a
      separate CLI process needs.
    - EgressCutter, CellEgress, EgressOutcome, LifecycleEgress, EGRESS_TIMEOUT_S: cutting a
      running Virtual Cell's egress to its control link alone for isolation, and restoring it,
      by declared capability (roadmap step 10.6a; hivemind.hive.egress).
    - CheckStatus, CheckResult, Attestation, CHECK_NAMES, attest, NightVeilProbe,
      FakeNightVeilProbe, SessionProbeConfig, SessionNightVeilProbe, run_checks, attest_cell:
      deterministic Night Veil bootstrap attestation, roadmap step 5.7b (hivemind.hive.night_veil).
"""

from hivemind.hive.backends import (
    BackendCapabilities,
    CellBackend,
    CellBootstrap,
    CellReadyInfo,
    DockerCellBackend,
    EgressCutter,
    FakeCellBackend,
    FakeReadinessGate,
    QemuCellBackend,
    QueenEndpoint,
    ReadinessGate,
    VirtualCellRecord,
    build_docker_backend,
    build_qemu_backend,
    mint_cell_bootstrap,
)
from hivemind.hive.cell_state import (
    TRANSITIONS,
    VirtualCellStatus,
    assert_dormant_allowed,
    assert_transition,
    can_enter_dormant,
    can_transition,
)
from hivemind.hive.egress import EGRESS_TIMEOUT_S, CellEgress, EgressOutcome, LifecycleEgress
from hivemind.hive.errors import (
    BackendCapabilityError,
    CellDestroyError,
    CellEgressError,
    CellProvisionError,
    HiveError,
    InvalidCellTransitionError,
    UnknownBackendError,
    UnknownCellError,
)
from hivemind.hive.lifecycle import (
    CellLifecycle,
    LifecycleDormantCell,
    LifecycleVirtualBackend,
    LiveVirtualCell,
    OverwinterSettings,
)
from hivemind.hive.models import CellReservation, NetworkPolicy, VirtualCellSpec
from hivemind.hive.night_veil import (
    CHECK_NAMES,
    Attestation,
    CheckResult,
    CheckStatus,
    FakeNightVeilProbe,
    NightVeilProbe,
    SessionNightVeilProbe,
    SessionProbeConfig,
    attest,
    attest_cell,
    run_checks,
)
from hivemind.hive.overwinter import (
    DormantCell,
    OverwinterConfig,
    OverwinterDecision,
    OverwinterPool,
    PooledCandidate,
    PoolView,
    ReleaseDecision,
    ReleaseOutcome,
    Scrubber,
    decide_release,
)
from hivemind.hive.registry import BackendRegistry, CellBackendFactory
from hivemind.hive.snapshot import (
    DockerSnapshotter,
    QemuSnapshotter,
    SnapshotLedger,
    SnapshotLedgerPort,
    SnapshotNotFoundError,
    SnapshotRecord,
    SqliteSnapshotLedger,
    snapshotter_for,
)

__all__ = [
    "CHECK_NAMES",
    "EGRESS_TIMEOUT_S",
    "TRANSITIONS",
    "Attestation",
    "BackendCapabilities",
    "BackendCapabilityError",
    "BackendRegistry",
    "CellBackend",
    "CellBackendFactory",
    "CellBootstrap",
    "CellDestroyError",
    "CellEgress",
    "CellEgressError",
    "CellLifecycle",
    "CellProvisionError",
    "CellReadyInfo",
    "CellReservation",
    "CheckResult",
    "CheckStatus",
    "DockerCellBackend",
    # roadmap step 5.10 (hivemind.hive.snapshot): appended as its own block for the same reason
    # the 5.9 block above is.
    "DockerSnapshotter",
    # roadmap step 5.9 (hivemind.hive.overwinter): appended as its own block, not interleaved
    # alphabetically above, so a concurrent edit to the rest of this list never conflicts with it.
    "DormantCell",
    "EgressCutter",
    "EgressOutcome",
    "FakeCellBackend",
    "FakeNightVeilProbe",
    "FakeReadinessGate",
    "HiveError",
    "InvalidCellTransitionError",
    "LifecycleDormantCell",
    "LifecycleEgress",
    "LifecycleVirtualBackend",
    "LiveVirtualCell",
    "NetworkPolicy",
    "NightVeilProbe",
    "OverwinterConfig",
    "OverwinterDecision",
    "OverwinterPool",
    "OverwinterSettings",
    "PoolView",
    "PooledCandidate",
    "QemuCellBackend",
    "QemuSnapshotter",
    "QueenEndpoint",
    "ReadinessGate",
    "ReleaseDecision",
    "ReleaseOutcome",
    "Scrubber",
    "SessionNightVeilProbe",
    "SessionProbeConfig",
    "SnapshotLedger",
    "SnapshotLedgerPort",
    "SnapshotNotFoundError",
    "SnapshotRecord",
    "SqliteSnapshotLedger",
    "UnknownBackendError",
    "UnknownCellError",
    "VirtualCellRecord",
    "VirtualCellSpec",
    "VirtualCellStatus",
    "assert_dormant_allowed",
    "assert_transition",
    "attest",
    "attest_cell",
    "build_docker_backend",
    "build_qemu_backend",
    "can_enter_dormant",
    "can_transition",
    "decide_release",
    "mint_cell_bootstrap",
    "run_checks",
    "snapshotter_for",
]
