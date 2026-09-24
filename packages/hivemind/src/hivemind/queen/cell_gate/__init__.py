"""The Queen-side half of a Virtual Cell's control link: readiness and the accepting listener.

A Virtual Cell dials out (ADR-0027); the Queen is the one side that listens, and this package is
that listener plus the `hivemind.hive.backends.bootstrap.ReadinessGate` implementation the
`CellBackend`s provision through. Placed under `queen`, not `hive`, because the Queen is the only
global view (codingrules section 4's layer table): `hive` (Layer 3) may never import `queen`
(Layer 6), and both `QueenReadinessGate` (accepts a `cell_id`-keyed key before the Cell's own
`node_id` exists) and `CellListener` (attaches the verified connection to a `Queen`) genuinely need
the Queen's own view.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Built by the composition root
    when `[virtual_cells] backend` is set; the `QueenReadinessGate` it builds is handed to every
    `CellBackend` that needs one, the `CellListener` it builds is started with the running
    `Queen`, and the `CellSnapshotHandler` it builds is handed to that same `CellListener` to
    answer the snapshot relay. Calls into `hivemind.cell`, `hivemind.forage`, `hivemind.hive.
    backends.bootstrap`, `hivemind.hive.lifecycle`, `hivemind.hive.snapshot`, `hivemind.queen.
    attach`, `hivemind.queen.deps`, `hivemind.queen.queen` and waggle only.

Key invariants:
    - `QueenReadinessGate` satisfies `hivemind.hive.backends.bootstrap.ReadinessGate` exactly:
      every `CellBackend` implementation can take one unmodified.
    - `CellListener` never attaches a connection whose first frame does not verify as a signed
      `CellReady` naming an `expect`-ed Cell.

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the connection
      direction this package's whole shape follows.
    - hivemind.hive.backends.bootstrap for the ReadinessGate Protocol QueenReadinessGate satisfies.
    - hivemind.queen.cell_gate.provider for LifecycleVirtualCellProvider, this package's own
      `hivemind.queen.deps.VirtualCellProvider` implementation (roadmap step 5.6).

Public API:
    - QueenReadinessGate: the real, Queen-side ReadinessGate (hivemind.queen.cell_gate.gate).
    - CellListener, CellListenerDeps: the accepting WebSocket listener
      (hivemind.queen.cell_gate.listener).
    - LifecycleVirtualCellProvider: the real VirtualCellProvider, over a CellLifecycle
      (hivemind.queen.cell_gate.provider).
    - make_on_task_finished: builds the callable QueenDeps.on_task_finished holds, for releasing a
      Virtual Cell once its task ends (hivemind.queen.cell_gate.release).
    - make_quiesce: builds the real `quiesce` callable make_on_task_finished calls before tearing a
      Cell down, so its Warden's final trail sync has a chance to land first
      (hivemind.queen.cell_gate.quiesce).
    - make_retire_all, RetireAll: builds the callable `run_hive` awaits at shutdown to tear down
      every Virtual Cell this process still tracks, dormant ones included
      (hivemind.queen.cell_gate.shutdown).
    - CellSnapshotHandler: answers a Warden's own CellSnapshotRequest/CellRollbackRequest, the
      snapshot relay's Queen-side half (hivemind.queen.cell_gate.snapshot).
"""

from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.listener import CellListener, CellListenerDeps, SnapshotRequestHandler
from hivemind.queen.cell_gate.provider import LifecycleVirtualCellProvider
from hivemind.queen.cell_gate.quiesce import make_quiesce
from hivemind.queen.cell_gate.release import make_on_cell_granted, make_on_task_finished
from hivemind.queen.cell_gate.shutdown import RetireAll, make_retire_all
from hivemind.queen.cell_gate.snapshot import CellSnapshotHandler

__all__ = [
    "CellListener",
    "CellListenerDeps",
    "CellSnapshotHandler",
    "LifecycleVirtualCellProvider",
    "QueenReadinessGate",
    "RetireAll",
    "SnapshotRequestHandler",
    "make_on_cell_granted",
    "make_on_task_finished",
    "make_quiesce",
    "make_retire_all",
]
