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
    - VirtualCellSpec, NetworkPolicy: a request to provision one Virtual Cell
      (hivemind.hive.models).
    - VirtualCellStatus, TRANSITIONS, can_transition, assert_transition, can_enter_dormant,
      assert_dormant_allowed: the Virtual Cell lifecycle state machine (hivemind.hive.cell_state).
    - HiveError, CellProvisionError, CellDestroyError, UnknownBackendError,
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
"""

from hivemind.hive.backends import (
    BackendCapabilities,
    CellBackend,
    CellBootstrap,
    CellReadyInfo,
    DockerCellBackend,
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
from hivemind.hive.errors import (
    BackendCapabilityError,
    CellDestroyError,
    CellProvisionError,
    HiveError,
    InvalidCellTransitionError,
    UnknownBackendError,
)
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from hivemind.hive.registry import BackendRegistry, CellBackendFactory

__all__ = [
    "TRANSITIONS",
    "BackendCapabilities",
    "BackendCapabilityError",
    "BackendRegistry",
    "CellBackend",
    "CellBackendFactory",
    "CellBootstrap",
    "CellDestroyError",
    "CellProvisionError",
    "CellReadyInfo",
    "DockerCellBackend",
    "FakeCellBackend",
    "FakeReadinessGate",
    "HiveError",
    "InvalidCellTransitionError",
    "NetworkPolicy",
    "QemuCellBackend",
    "QueenEndpoint",
    "ReadinessGate",
    "UnknownBackendError",
    "VirtualCellRecord",
    "VirtualCellSpec",
    "VirtualCellStatus",
    "assert_dormant_allowed",
    "assert_transition",
    "build_docker_backend",
    "build_qemu_backend",
    "can_enter_dormant",
    "can_transition",
    "mint_cell_bootstrap",
]
