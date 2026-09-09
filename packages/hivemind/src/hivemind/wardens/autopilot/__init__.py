"""Provide the Warden's Autopilot: a deterministic dispatch table that never awaits a model.

This keeps the Hive working when every provider is down (codingrules section 8.8). `WardenAction`
(`actions.py`) is the closed set of moves the table can pick; `decide` (`table.py`) is the pure
function `hivemind.wardens.warden.Warden`'s tick calls once per ordered inbox item, mapping a
`TaskAssign`, a `GrantIssued`, a sub-bee's claimed `TaskResult`, an `AlarmRaised` (through
`hivemind.supervision.policy.decide`), a `Question`/`Answer`, a control message, routine
`TaskProgress`/`Heartbeat` traffic, or anything unrecognised (`NEEDS_JUDGEMENT`) onto one
`WardenAction`. Nothing under this package may import `hivemind.llm`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles the Warden's deterministic fallback; never imports hivemind.llm. Called by
    `hivemind.wardens.warden.Warden`'s tick, once per ordered inbox item.

Key invariants:
    - `decide` never imports `hivemind.llm`, directly or transitively (`lint-imports` enforces it
      for every module under an `autopilot/` directory, codingrules section 4).
    - `decide` is pure: the same `(item, sub_bee, policy)` always returns the same `WardenAction`.

See Also:
    - .claude/codingrules.md section 4 for "autopilot never imports hivemind.llm".
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this package
      implements.
    - .claude/roadmap.md phase 3 step 3.19 for the work that first populates this package.

Public API (roadmap step 3.19):
    - WardenAction: the closed set of moves the dispatch table can pick (actions).
    - SubBeeView, decide: the dispatch table itself (table).
"""

from hivemind.wardens.autopilot.actions import WardenAction
from hivemind.wardens.autopilot.table import SubBeeView, decide

__all__ = ["SubBeeView", "WardenAction", "decide"]
