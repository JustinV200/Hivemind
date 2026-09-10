"""Provide the Queen's Autopilot: a deterministic dispatch table that never awaits a model.

This keeps the Hive working when every provider is down (codingrules section 8.8). `QueenAction`
(`actions.py`) is the closed set of moves the table can pick; `decide` (`table.py`) is the pure
function `hivemind.queen.queen.Queen`'s tick calls once per ordered inbox item, mapping a
`Heartbeat`, a `TaskResult`, an `AlarmRaised` (through `hivemind.supervision.policy.decide`, capped
by the Queen's own attempt ceiling), a `Question`, an `Answer`, or anything unrecognised
(`NEEDS_JUDGEMENT`) onto one `QueenAction`; `effort_for` (`effort.py`) sets the `Effort` any
resulting awake episode runs at, by event class. Nothing under this package may import
`hivemind.llm`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles the Queen's deterministic fallback; never imports hivemind.llm. Called by
    `hivemind.queen.queen.Queen`'s tick, once per ordered inbox item.

Key invariants:
    - `decide` never imports `hivemind.llm`, directly or transitively (`lint-imports` enforces it
      for every module under an `autopilot/` directory, codingrules section 4).
    - `decide` is pure: the same `(item, task, attempts, policy, limit)` always returns the same
      `QueenAction`.

See Also:
    - .claude/codingrules.md section 4 for "autopilot never imports hivemind.llm".
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this package
      implements.
    - .claude/roadmap.md phase 3 step 3.20 for the work that first populates this package.

Public API (roadmap step 3.20):
    - QueenAction: the closed set of moves the dispatch table can pick (actions).
    - decide: the dispatch table itself (table).
    - effort_for: the Effort an awake episode gets, by event class (effort).
"""

from hivemind.queen.autopilot.actions import QueenAction
from hivemind.queen.autopilot.effort import effort_for
from hivemind.queen.autopilot.table import decide

__all__ = ["QueenAction", "decide", "effort_for"]
