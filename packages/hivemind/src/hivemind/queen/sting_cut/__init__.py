"""Sting Cut one Cell: the emergency disconnect, the human's for any Cell (roadmap step 13.4a).

A Sting Cut revokes a Cell's lease and grants, kills the processes the Hive started for the lease,
removes its scratch, forgets its key and, for a Night Veil Cell, tears it down so its retained
records go with it; `cell.sting_cut` records who, why and what each step did. The human cuts any
Cell, the Hive Stand's own included, after a step-up and the typed phrase (`door`). The Queen cuts
one only for a dire pattern under `[guard.sting_cut]`, after an isolation, within her hourly cap,
with evidence kept first, and never the Hive Stand's lease (`authority`). One path (`path`) does
it for both; `steps` holds its effects, `record` its trail reads and writes, and `hands` the seam
the Virtual side fills.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by the Queen's Guard request decision (`hivemind.queen.guard_requests.decision`) and the Hive
    Entrance's Sting Cut route (through `StingCutDoor`, which the Queen inherits). Calls into
    `hivemind.cell`, `hivemind.forage`, `hivemind.guard`, `hivemind.hive`, `hivemind.pheromone`,
    `hivemind.queen.errors`, `hivemind.queen.forage`, `hivemind.queen.guard_requests`,
    `hivemind.queen.isolation`, `hivemind.supervision` and waggle.

Key invariants:
    - Every cut goes through `sting_cut_cell`; nothing composes its steps by hand elsewhere.
    - The Queen never cuts the Hive Stand's lease, a Cell not isolated, or past her cap.

See Also:
    - .claude/roadmap.md step 13.4a; docs/guard/sting-cut.md for the operator's view.

Public API:
    - sting_cut_cell: the one path (path).
    - StingCutDoor: the human's lever as a Queen method (door).
    - StingCutOrder, StingCutOutcome, StingCutRefusal, Cutter, ScrubResult,
      MAX_STING_CUT_REASON_CHARS: the order and its answer (order).
    - CellCut: the Virtual side's seam (hands).
    - STING_CUT_KIND, read_cut, cut_cell_ids, queen_cuts_in_window: the trail (record).
"""

from hivemind.queen.sting_cut.door import StingCutDoor
from hivemind.queen.sting_cut.hands import CellCut
from hivemind.queen.sting_cut.order import (
    MAX_STING_CUT_REASON_CHARS,
    Cutter,
    ScrubResult,
    StingCutOrder,
    StingCutOutcome,
    StingCutRefusal,
)
from hivemind.queen.sting_cut.path import sting_cut_cell
from hivemind.queen.sting_cut.record import (
    STING_CUT_KIND,
    cut_cell_ids,
    queen_cuts_in_window,
    read_cut,
)

__all__ = [
    "MAX_STING_CUT_REASON_CHARS",
    "STING_CUT_KIND",
    "CellCut",
    "Cutter",
    "ScrubResult",
    "StingCutDoor",
    "StingCutOrder",
    "StingCutOutcome",
    "StingCutRefusal",
    "cut_cell_ids",
    "queen_cuts_in_window",
    "read_cut",
    "sting_cut_cell",
]
