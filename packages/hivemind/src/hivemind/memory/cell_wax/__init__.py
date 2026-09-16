"""Cell Wax: Queen-written cautions about one Cell (roadmap step 4.2a).

A Cell Wax note marks one Cell (a unit of compute: a Virtual Cell the Hive provisions, or a Real
Cell, an existing device borrowed for a task and left exactly as found) the way wax bees use to
cap and mend a cell: it marks the cell, it is not the honey inside. Anyone -- a bee, a Warden or
the human -- may propose one; only the Queen writes, rejects or clears it. `model` holds `CellWax`
(the note itself, at rest) and `WaxSeverity` (mirrored from `waggle.messages.cell.wax` member for
member); `state` holds `WaxState` and the one transition table, `PROPOSED -> WRITTEN ->
CLEARED | EXPIRED`, `PROPOSED -> REJECTED` (Appendix C); `writes` holds the five functions that
walk that table -- `propose_wax`, `write_wax`, `reject_wax`, `clear_wax`, `expire_wax` -- plus
`retire_wax_for_cell`, phase 5's own forward-looking hook for a destroyed Virtual Cell.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory`. Called by
    `hivemind.queen.ticks.wax` (a proposal arriving, and the autopilot/awake decision on it) and
    `hivemind.workers.roles.house_bee.sweep` (expiry). Calls into `hivemind.cell`, `hivemind.
    memory.context`, `hivemind.memory.errors`, `hivemind.pheromone` and waggle only; never
    `hivemind.brood_chamber` or `hivemind.supervision` (codingrules section 4).

Key invariants:
    - Only the Queen ever calls `write_wax`, `reject_wax`, `clear_wax` or `expire_wax`
      (`hivemind.queen.ticks.wax`, `hivemind.workers.roles.house_bee.sweep`); every other bee that
      wants to affect a note's life calls `propose_wax` and waits.
    - Every write function commits its `CellWax` row and its `memory.wax_*` trail event in the
      same store call (codingrules section 12).

See Also:
    - .claude/roadmap.md step 4.2a for this package's spec verbatim.
    - .claude/codingrules.md section 6.1, "Cell Wax" row.
    - .claude/codingrules.md Appendix C, "Cell Wax note" row, for the transition table.
    - docs/adr/0022-memory-tiers-relevance-and-compaction.md for the per-Cell hot-state cap.
    - waggle.messages.cell.wax for the wire messages this package's writes correspond to.

Public API:
    - CellWax, WaxSeverity, MAX_WAX_REASON_CHARS, MAX_WAX_TEXT_CHARS, cap_wax_for_hot_state,
      new_wax_id: the note itself, at rest (model).
    - WaxState, TRANSITIONS, can_transition, assert_transition, is_terminal: the state machine
      (state).
    - WaxProposalInput, propose_wax, write_wax, reject_wax, clear_wax, expire_wax,
      retire_wax_for_cell: the functions that walk it (writes).
"""

from hivemind.memory.cell_wax.model import (
    MAX_WAX_REASON_CHARS,
    MAX_WAX_TEXT_CHARS,
    CellWax,
    WaxSeverity,
    cap_wax_for_hot_state,
    new_wax_id,
)
from hivemind.memory.cell_wax.state import (
    TERMINAL_STATES,
    TRANSITIONS,
    WaxState,
    assert_transition,
    can_transition,
    is_terminal,
)
from hivemind.memory.cell_wax.writes import (
    WaxProposalInput,
    clear_wax,
    expire_wax,
    propose_wax,
    reject_wax,
    retire_wax_for_cell,
    write_wax,
)

__all__ = [
    "MAX_WAX_REASON_CHARS",
    "MAX_WAX_TEXT_CHARS",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "CellWax",
    "WaxProposalInput",
    "WaxSeverity",
    "WaxState",
    "assert_transition",
    "can_transition",
    "cap_wax_for_hot_state",
    "clear_wax",
    "expire_wax",
    "is_terminal",
    "new_wax_id",
    "propose_wax",
    "reject_wax",
    "retire_wax_for_cell",
    "write_wax",
]
