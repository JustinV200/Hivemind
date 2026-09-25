"""An isolated Cell's Warden: taint its own memory on the Queen's order, refuse a tainted resume.

Only the Queen isolates a Cell (ADR-0043, roadmap step 10.6a), and her isolation taints the memory
written there from the report's first cited event on. A Virtual Cell's Warden (the supervisor of
one Cell) keeps its own memory store inside the Cell (ADR-0027), which her label on the Hive's
tables cannot reach, so she orders it (`waggle.messages.cell.CellTaintOrder`) and it runs the same
setter over its own store (`taint`). From then on its resume gate (`gate`) refuses any assignment
that would resume a bee from a Handoff so labelled, until a judge clears it: a lift restores the
Cell's placement and egress, never a resume from what the isolation tainted.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Called by `hivemind.wardens.warden.Warden`'s tick. Calls into `hivemind.cell`,
    `hivemind.common.logging`, `hivemind.guard`, `hivemind.memory`, `hivemind.wardens.ticks.
    trail_ship` and waggle; no tick module imports this package.

Key invariants:
    - Only `taint` labels memory here, with `TaintSource.ISOLATION` alone (tests/unit/memory/
      taint/test_only_setter.py allow-lists exactly that module).
    - A resume from a TAINTED Handoff never spawns on this Warden.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.queen.isolation for the one isolation path, which sends the order.
    - docs/guard/isolation.md.

Public API (roadmap step 10.6a):
    - taint_own_memory: the in-Cell isolation setter, on the Queen's order (taint).
    - admit_resume, TAINTED_HANDOFF_SCOPE: the resume gate and its refusal's rule (gate).
"""

from hivemind.wardens.isolation.gate import TAINTED_HANDOFF_SCOPE, admit_resume
from hivemind.wardens.isolation.taint import taint_own_memory

__all__ = ["TAINTED_HANDOFF_SCOPE", "admit_resume", "taint_own_memory"]
