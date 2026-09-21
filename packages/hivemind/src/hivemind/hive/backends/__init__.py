"""Hold one CellBackend implementation per kind of infrastructure a Virtual Cell can use.

Covers local containers, a local hypervisor, and cloud providers under backends/cloud/.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the hive package. Handles
    one CellBackend implementation per local kind of infrastructure. Called by hive's public API
    on behalf of whatever calls hive itself; calls into sibling packages at Layer 3 or below,
    never back up into hive's other sub-packages directly.

Key invariants:
    - Every Cell a CellBackend implementation returns has kind == CellKind.VIRTUAL and
      access_level == AccessLevel.FULL (hivemind.cell.Cell's own validator enforces the latter).

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under hive.
    - .claude/roadmap.md phase 5 for the work that populates it: step 5.2 (base.py, fake.py, this
      face), step 5.4 (docker.py), step 5.11 (qemu.py).

Public API:
    - CellBackend: protocol every provisioning backend implements (hivemind.hive.backends.base).
    - BackendCapabilities, VirtualCellRecord: the capability-declaration and list_cells value
      types every implementation shares (hivemind.hive.backends.base).
    - FakeCellBackend: the in-memory reference implementation (hivemind.hive.backends.fake).
"""

from hivemind.hive.backends.base import BackendCapabilities, CellBackend, VirtualCellRecord
from hivemind.hive.backends.fake import FakeCellBackend

__all__ = ["BackendCapabilities", "CellBackend", "FakeCellBackend", "VirtualCellRecord"]
