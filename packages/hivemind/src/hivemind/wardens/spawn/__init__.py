"""Start a new Worker on the Warden's Cell and hand it a CellSession.

This is how a Warden starts that Worker and hands it the CellSession it needs. `SubBee`
(`sub_bee.py`) is the Warden's own bookkeeping row for one spawned Worker: its mirrored
`WorkerState`, its current binding, its last Handoff, and its own `WorkerRuntime` plus the task
running it. `spawn_sub_bee` (`spawn.py`) is the whole start-up sequence: attenuate capabilities,
carve a grant slice, resolve the assignment's slot to a live model, build the Capping gate and
`WorkerContext`, start the `WorkerRuntime` and send the sub-bee its first `TaskAssign`;
`stop_sub_bee` (`spawn.py`) is its counterpart, stopping that runtime cooperatively before falling
back to a bounded cancel (codingrules section 11: never left cancelled-but-unawaited).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles starting and stopping one Worker on the Warden's Cell. `spawn_sub_bee` is called by
    `hivemind.wardens.ticks.assign` and `hivemind.wardens.ticks.alarms`; `stop_sub_bee` is called
    by `hivemind.wardens.warden.Warden.stop`.

Key invariants:
    - `spawn_sub_bee`'s runtime task is returned, never dropped: its caller tracks, awaits or
      cancels it (codingrules section 11).
    - `stop_sub_bee` never returns with that task still pending, whichever path it took to finish.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/roadmap.md phase 3 step 3.19 for the work that first populates it.

Public API (roadmap step 3.19):
    - SubBee: the Warden's own bookkeeping row for one sub-bee (sub_bee).
    - WardenCellContext, spawn_sub_bee, stop_sub_bee: start and stop one sub-bee within a grant
      (spawn).
"""

from hivemind.wardens.spawn.spawn import WardenCellContext, spawn_sub_bee, stop_sub_bee
from hivemind.wardens.spawn.sub_bee import SubBee

__all__ = ["SubBee", "WardenCellContext", "spawn_sub_bee", "stop_sub_bee"]
