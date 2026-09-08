"""Re-export the cell family: a Cell's own life, its leases and the Cell Wax notes about it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A Cell is
one unit of compute, real (a borrowed device) or virtual (a VM or container), and its Warden (the
always-on supervisor of one Cell) speaks for it. ``status`` holds the Cell's own life (ready, its
heartbeat, its needs report); ``leases`` the tenancy lifecycle (a Cell asked for, a lease opened, a
teardown asked for, a lease released); ``wax`` the Cell Wax notes, Queen-written cautions about one
Cell, proposed, written and cleared. This package is the family's face: a caller imports any of its
messages, enums or value models from here without knowing which module defines them. The bounds
each module names stay in that module, because the spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a cell payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``cell.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.5 for the family's normative fields and rules.
    - waggle.messages.cell.status, waggle.messages.cell.leases and waggle.messages.cell.wax for
      the definitions.

Public API:
    - Status (status): AttestationCheck, CellHeartbeat, CellMode, CellReady, IsolationNeed,
      ReleaseCause, TaskNeedsReport.
    - Leases (leases): CellRequest, CellTeardownRequest, LeaseOpened, LeaseReleased.
    - Wax (wax): CellWaxCleared, CellWaxProposed, CellWaxWritten, WaxClearCause, WaxDecision,
      WaxOrigin, WaxSeverity.
"""

from waggle.messages.cell.leases import CellRequest, CellTeardownRequest, LeaseOpened, LeaseReleased
from waggle.messages.cell.status import (
    AttestationCheck,
    CellHeartbeat,
    CellMode,
    CellReady,
    IsolationNeed,
    ReleaseCause,
    TaskNeedsReport,
)
from waggle.messages.cell.wax import (
    CellWaxCleared,
    CellWaxProposed,
    CellWaxWritten,
    WaxClearCause,
    WaxDecision,
    WaxOrigin,
    WaxSeverity,
)

__all__ = [
    "AttestationCheck",
    "CellHeartbeat",
    "CellMode",
    "CellReady",
    "CellRequest",
    "CellTeardownRequest",
    "CellWaxCleared",
    "CellWaxProposed",
    "CellWaxWritten",
    "IsolationNeed",
    "LeaseOpened",
    "LeaseReleased",
    "ReleaseCause",
    "TaskNeedsReport",
    "WaxClearCause",
    "WaxDecision",
    "WaxOrigin",
    "WaxSeverity",
]
