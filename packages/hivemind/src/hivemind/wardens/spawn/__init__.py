"""Start a new Worker on the Warden's Cell and hand it a CellSession.

This is how a Warden starts that Worker and hands it the CellSession it needs. `SubBee`
(`sub_bee.py`) is the Warden's own bookkeeping row for one spawned Worker: its mirrored
`WorkerState`, its current binding, its last Handoff and the owned runtime task and transport
link. `spawn_sub_bee` (`spawn.py`) is the whole start-up sequence: attenuate capabilities, carve a
grant slice, resolve the assignment's slot to a live model, build the Capping gate and
`WorkerContext`, start the `WorkerRuntime` and send the sub-bee its first `TaskAssign`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles starting a new Worker on the Warden's Cell. Called by `hivemind.wardens.ticks.assign`
    and `hivemind.wardens.ticks.alarms`.

Key invariants:
    - `spawn_sub_bee`'s runtime task is returned, never dropped: its caller tracks, awaits or
      cancels it (codingrules section 11).

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/roadmap.md phase 3 step 3.19 for the work that first populates it.

Public API (roadmap step 3.19):
    - SubBee: the Warden's own bookkeeping row for one sub-bee (sub_bee).
    - WardenCellContext, spawn_sub_bee: start one new sub-bee within a grant (spawn).
"""

from hivemind.wardens.spawn.spawn import WardenCellContext, spawn_sub_bee
from hivemind.wardens.spawn.sub_bee import SubBee

__all__ = ["SubBee", "WardenCellContext", "spawn_sub_bee"]
