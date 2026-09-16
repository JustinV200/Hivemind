"""Provide the Queen's Autopilot: a deterministic dispatch table that never awaits a model.

This keeps the Hive working when every provider is down (codingrules section 8.8). `QueenAction`
(`actions.py`) is the closed set of moves the table can pick; `decide` (`table.py`) is the pure
function `hivemind.queen.queen.Queen`'s tick calls once per ordered inbox item, mapping a
`Heartbeat`, a `TaskResult`, an `AlarmRaised` (through `hivemind.supervision.policy.decide`, capped
by the Queen's own attempt ceiling), a `Question`, an `Answer`, or anything unrecognised
(`NEEDS_JUDGEMENT`) onto one `QueenAction`; `effort_for` (`effort.py`) sets the `Effort` any
resulting awake episode runs at, by event class. `ForageAutopilotOutcome`, `ForageRequestSignal`
and `decide_forage_request` (`forage.py`, roadmap step 4.7) are the sibling rule for a
`waggle.messages.forage.ForageRequest`: `hivemind.queen.ticks.forage` calls it directly, ahead of
`decide`, since `decide`'s own fallback for an unrecognised payload type is `NEEDS_JUDGEMENT`
outright -- exactly what "within headroom, no awake episode" must not be. `WaxAutopilotOutcome`,
`WaxProposalSignal` and `decide_wax_proposal` (`wax.py`, roadmap step 4.2a) are the same shape of
sibling rule for a `waggle.messages.cell.CellWaxProposed`: `hivemind.queen.ticks.wax` calls it
directly too, ahead of `decide`, so a Warden's own NOTE or CAUTION about its own Cell is written
by autopilot with no awake episode. Nothing under this package may import `hivemind.llm`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles the Queen's deterministic fallback; never imports hivemind.llm. Called by
    `hivemind.queen.queen.Queen`'s tick, once per ordered inbox item, and by
    `hivemind.queen.ticks.forage`/`hivemind.queen.ticks.wax` for a ForageRequest/CellWaxProposed
    specifically.

Key invariants:
    - `decide` never imports `hivemind.llm`, directly or transitively (`lint-imports` enforces it
      for every module under an `autopilot/` directory, codingrules section 4).
    - `decide` is pure: the same `(item, task, attempts, policy, limit)` always returns the same
      `QueenAction`; `decide_forage_request` and `decide_wax_proposal` are pure the same way, each
      over its own single argument.

See Also:
    - .claude/codingrules.md section 4 for "autopilot never imports hivemind.llm".
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this package
      implements.
    - .claude/roadmap.md phase 3 step 3.20 for the work that first populated this package; phase 4
      step 4.7 for the ForageRequest rule; step 4.2a for the Cell Wax rule.

Public API (roadmap steps 3.20, 4.7, 4.2a):
    - QueenAction: the closed set of moves the dispatch table can pick (actions).
    - decide: the dispatch table itself (table).
    - effort_for: the Effort an awake episode gets, by event class (effort).
    - ForageAutopilotOutcome, ForageRequestSignal, decide_forage_request: the ForageRequest rule
      (forage).
    - WaxAutopilotOutcome, WaxProposalSignal, decide_wax_proposal: the Cell Wax proposal rule
      (wax).
"""

from hivemind.queen.autopilot.actions import QueenAction
from hivemind.queen.autopilot.effort import effort_for
from hivemind.queen.autopilot.forage import (
    ForageAutopilotOutcome,
    ForageRequestSignal,
    decide_forage_request,
)
from hivemind.queen.autopilot.table import decide
from hivemind.queen.autopilot.wax import WaxAutopilotOutcome, WaxProposalSignal, decide_wax_proposal

__all__ = [
    "ForageAutopilotOutcome",
    "ForageRequestSignal",
    "QueenAction",
    "WaxAutopilotOutcome",
    "WaxProposalSignal",
    "decide",
    "decide_forage_request",
    "decide_wax_proposal",
    "effort_for",
]
