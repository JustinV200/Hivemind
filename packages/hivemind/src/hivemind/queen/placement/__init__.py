"""Decide whether a task's TaskNeeds are met by a Real Cell or a new Virtual one.

TaskNeeds are what a task requires from the Cell that runs it; this is the Queen's pure decision
of whether to reuse a Real Cell or provision a new Virtual one. Roadmap step 3.20's own v0: the
Hive Stand only, so `decide` (`decide.py`) reduces to two checks over the first attached
`hivemind.queen.deps.WardenLink` -- isolation and operating system -- and raises `PlacementError`
when neither can be satisfied.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles the pure decision of Real vs Virtual Cell for a task's TaskNeeds. Called by
    `hivemind.queen.dispatcher.dispatch_ready`.

Key invariants:
    - `decide` is pure: the same `(needs, wardens)` always returns the same `Placement`.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/codingrules.md section 8.7 for "placement is a pure decision".
    - .claude/roadmap.md phase 3 step 3.20 for the work that first populates it.

Public API (roadmap step 3.20):
    - Placement, PlacementError, decide: the placement decision itself (decide).
"""

from hivemind.queen.placement.decide import Placement, PlacementError, decide

__all__ = ["Placement", "PlacementError", "decide"]
