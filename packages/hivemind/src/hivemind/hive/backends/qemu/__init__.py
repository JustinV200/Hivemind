"""Provision Virtual Cells as real QEMU VMs: the second CellBackend (ADR-0026, roadmap step 5.11).

Docker (`hivemind.hive.backends.docker`) gives `isolation = "preferred"` the fastest path to a
working Virtual Cell on every supported host; this package gives `isolation = "required"` a real
hypervisor boundary instead, over a prebuilt qcow2 (`images/base-ubuntu/vm/`, built by
`scripts/build_cell_image.py`) and cloud-init's NoCloud datasource for first-boot configuration.
Same `hivemind.hive.backends.base.CellBackend` contract, same
`hivemind.hive.backends.bootstrap.CellBootstrap`/`ReadinessGate` seam, same shape as `docker/`:
a narrow client Protocol (`QemuRunnerPort`) with a real implementation (`ProcessQemuRunner`) and an
in-memory fake (`FakeQemuRunner`), a pure `network.py` mapping `NetworkPolicy` onto QEMU's own
user-mode networking, and `backend.py`'s `QemuCellBackend`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends`. Called by `hive`'s public API
    (`hivemind.hive.registry.BackendRegistry`, once a composition root registers it) on behalf of
    whatever calls `hive` itself. Calls into `hivemind.cell`, `hivemind.hive.backends.base`,
    `hivemind.hive.backends.bootstrap`, `hivemind.hive.cell_state`, `hivemind.hive.errors`,
    `hivemind.hive.models` and `waggle` only.

Key invariants:
    - Every Cell `QemuCellBackend` returns has kind == CellKind.VIRTUAL and access_level ==
      AccessLevel.FULL (`hivemind.cell.Cell`'s own validator enforces the latter).
    - QEMU is not installed on the machine this package was authored on (ADR-0026): every module
      here is unit-testable with no real `qemu-img`/`qemu-system-x86_64` present, through
      `FakeQemuRunner`; `packages/hivemind/tests/integration/test_qemu_backend.py` is the one
      place real QEMU is exercised, marked `@pytest.mark.integration` and skipping cleanly when
      `qemu-system-x86_64`/`qemu-img` are absent.
    - Nothing in this package registers a backend at import time (codingrules 5.5): `build_qemu_
      backend` is the factory a composition root calls and hands to `BackendRegistry.register`.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md
    - .claude/roadmap.md step 5.11 for the work that populates this package.
    - hivemind.hive.backends.docker for the reference backend this package's shape mirrors.
    - scripts/build_cell_image.py for how images/base-ubuntu/vm/base-ubuntu.qcow2 is built.

Public API:
    - QemuRunnerPort, QemuVmSpec, QemuVmHandle, QemuVmRecord, QemuRunnerError, vm_dir_for: the
      narrow client Protocol and its value types (hivemind.hive.backends.qemu.runner).
    - plan_network, QemuNetworkPlan: NetworkPolicy -> QEMU user-mode networking mapping
      (hivemind.hive.backends.qemu.network).
    - render_user_data, render_meta_data, READINESS_MARKER: the cloud-init NoCloud documents
      (hivemind.hive.backends.qemu.cloud_init).
    - ProcessQemuRunner, probe_accelerator: the real implementation
      (hivemind.hive.backends.qemu.process_runner).
    - FakeQemuRunner: the in-memory reference implementation (hivemind.hive.backends.qemu.fake).
    - QemuCellBackend, QemuBackendConfig, build_qemu_backend: the CellBackend, its bundled
      base-image/vm-root/headroom config, and its registry factory
      (hivemind.hive.backends.qemu.backend).
"""

from hivemind.hive.backends.qemu.backend import (
    QemuBackendConfig,
    QemuCellBackend,
    build_qemu_backend,
)
from hivemind.hive.backends.qemu.cloud_init import (
    ENV_FILE_PATH,
    ENV_QUEEN_WAGGLE_URL_KEY,
    READINESS_MARKER,
    SIGNING_KEY_FILE_PATH,
    SYSTEMD_UNIT_NAME,
    SYSTEMD_UNIT_PATH,
    render_meta_data,
    render_user_data,
)
from hivemind.hive.backends.qemu.fake import FakeQemuRunner
from hivemind.hive.backends.qemu.network import (
    ALLOWLIST_LABEL,
    GUEST_CONTROL_ADDR,
    GUEST_CONTROL_PORT,
    USER_NET_HOST_ALIAS,
    QemuNetworkPlan,
    plan_network,
)
from hivemind.hive.backends.qemu.process_runner import ProcessQemuRunner, probe_accelerator
from hivemind.hive.backends.qemu.runner import (
    QemuRunnerError,
    QemuRunnerPort,
    QemuVmHandle,
    QemuVmRecord,
    QemuVmSpec,
    vm_dir_for,
)

__all__ = [
    "ALLOWLIST_LABEL",
    "ENV_FILE_PATH",
    "ENV_QUEEN_WAGGLE_URL_KEY",
    "GUEST_CONTROL_ADDR",
    "GUEST_CONTROL_PORT",
    "READINESS_MARKER",
    "SIGNING_KEY_FILE_PATH",
    "SYSTEMD_UNIT_NAME",
    "SYSTEMD_UNIT_PATH",
    "USER_NET_HOST_ALIAS",
    "FakeQemuRunner",
    "ProcessQemuRunner",
    "QemuBackendConfig",
    "QemuCellBackend",
    "QemuNetworkPlan",
    "QemuRunnerError",
    "QemuRunnerPort",
    "QemuVmHandle",
    "QemuVmRecord",
    "QemuVmSpec",
    "build_qemu_backend",
    "plan_network",
    "probe_accelerator",
    "render_meta_data",
    "render_user_data",
    "vm_dir_for",
]
