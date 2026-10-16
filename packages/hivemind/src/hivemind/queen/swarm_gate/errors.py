"""Define RECOVERABLE_ERRORS: what one gate pass, or one flow, survives without ending the gate.

The swarm gate runs its reconcile loop, its flows and its Level 0 Wardens in a `TaskGroup` beside
the Queen's tick, and both sides move the same Brood Chamber rows. A pass that raises something
the loop does not know ends the loop, and with it every Warden and flow the group holds, and then
the Hive (`run_hive`'s own group cancels the Queen). So the set of what a pass or a flow logs and
goes on from is one tuple, named here, and every catch in the gate uses it: the errors of a device
and its link (`SwarmError`), of a Cell (`CellError`), of the stores (`sqlite3.Error`), and of the
chamber itself. A chamber conflict is ordinary here: a pause that waited on a Handoff finds the
task finished by the tick (`InvalidTransitionError`), a resume finds a task the human cancelled, a
take-back finds a row gone (`TaskNotFoundError`). Those are two `HiveMindError`s that are not
`BroodChamberError`s (they share the common conflict and not-found roots), so all three are named.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Read by `.gate` (the loop's recoverable set), `.reconcile` (one node's sync) and the flows of
    `.colonize`. Calls into `hivemind.brood_chamber`, `hivemind.cell` and `hivemind.swarm` for the
    types only.

Key invariants:
    - Every catch that keeps the gate running names this tuple, never a subset of its own, so an
      error type that one path survives is survived by every path.
    - `asyncio.CancelledError` is never in it: cancellation is how the gate stops.

See Also:
    - hivemind.queen.swarm_gate.gate for the loop that would otherwise end.
    - hivemind.queen.cluster.protocol for the pause that re-reads the task before it pauses.
"""

from __future__ import annotations

import sqlite3

from hivemind.brood_chamber import BroodChamberError, InvalidTransitionError, TaskNotFoundError
from hivemind.cell import CellError
from hivemind.swarm import SwarmError

__all__ = ["CHAMBER_CONFLICTS", "RECOVERABLE_ERRORS"]

# A chamber row that moved under a pass or a flow: the task is left as it is and the work goes on.
CHAMBER_CONFLICTS: tuple[type[Exception], ...] = (
    BroodChamberError,
    InvalidTransitionError,
    TaskNotFoundError,
)

# Everything a pass or a flow logs and survives; the loop's own set and every flow's catch.
RECOVERABLE_ERRORS: tuple[type[Exception], ...] = (
    SwarmError,
    CellError,
    sqlite3.Error,
    *CHAMBER_CONFLICTS,
)
