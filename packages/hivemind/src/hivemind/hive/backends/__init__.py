"""Hold one CellBackend implementation per kind of infrastructure a Virtual Cell can use.

Covers local containers (`docker/`), a local hypervisor (`qemu/`), and cloud providers under
`cloud/`. `bootstrap.py` holds the backend-independent identity and readiness seam
(`CellBootstrap`, `QueenEndpoint`, `ReadinessGate`, `CellReadyInfo`) every backend provisions
through, so the Docker, QEMU and cloud backends share one shape without sharing infrastructure
code.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the hive package. Handles
    one CellBackend implementation per local kind of infrastructure. Called by hive's public API
    on behalf of whatever calls hive itself; calls into sibling packages at Layer 3 or below,
    never back up into hive's other sub-packages directly.

Key invariants:
    - Every Cell a CellBackend implementation returns has kind == CellKind.VIRTUAL and
      access_level == AccessLevel.FULL (hivemind.cell.Cell's own validator enforces the latter).
    - Nothing in this package registers a backend at import time (codingrules 5.5): each
      implementation exports a factory (`hivemind.hive.backends.docker.build_docker_backend`,
      `hivemind.hive.backends.qemu.build_qemu_backend`) the composition root calls and hands to
      `hivemind.hive.registry.BackendRegistry.register`.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under hive.
    - .claude/roadmap.md phase 5 for the work that populates it: step 5.2 (base.py, fake.py, this
      face), step 5.4 (docker/), step 5.11 (qemu/), step 5.12 (cloud/).

Public API:
    - CellBackend: protocol every provisioning backend implements (hivemind.hive.backends.base).
    - BackendCapabilities, VirtualCellRecord: the capability-declaration and list_cells value
      types every implementation shares (hivemind.hive.backends.base).
    - FakeCellBackend: the in-memory reference implementation (hivemind.hive.backends.fake).
    - CellBootstrap, QueenEndpoint, ReadinessGate, CellReadyInfo, mint_cell_bootstrap: the
      backend-independent provisioning seam (hivemind.hive.backends.bootstrap).
    - FakeReadinessGate: the in-memory ReadinessGate (hivemind.hive.backends.fake).
    - DockerCellBackend, build_docker_backend: the Docker backend and its registry factory
      (hivemind.hive.backends.docker).
    - QemuCellBackend, build_qemu_backend: the QEMU backend and its registry factory
      (hivemind.hive.backends.qemu).
    - hivemind.hive.backends.cloud: the (optional, post-1.0) cloud provider seam, reached
      directly, not re-exported here -- see that package's own README.
"""

from hivemind.hive.backends.base import BackendCapabilities, CellBackend, VirtualCellRecord
from hivemind.hive.backends.bootstrap import (
    CellBootstrap,
    CellReadyInfo,
    QueenEndpoint,
    ReadinessGate,
    mint_cell_bootstrap,
)
from hivemind.hive.backends.docker import DockerCellBackend, build_docker_backend
from hivemind.hive.backends.fake import FakeCellBackend, FakeReadinessGate
from hivemind.hive.backends.qemu import QemuCellBackend, build_qemu_backend

__all__ = [
    "BackendCapabilities",
    "CellBackend",
    "CellBootstrap",
    "CellReadyInfo",
    "DockerCellBackend",
    "FakeCellBackend",
    "FakeReadinessGate",
    "QemuCellBackend",
    "QueenEndpoint",
    "ReadinessGate",
    "VirtualCellRecord",
    "build_docker_backend",
    "build_qemu_backend",
    "mint_cell_bootstrap",
]
