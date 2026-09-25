"""Start a new Worker on the Warden's Cell and hand it a CellSession.

This is how a Warden starts that Worker and hands it the CellSession it needs. `SubBee`
(`sub_bee.py`) is the Warden's own bookkeeping row for one spawned Worker: its mirrored
`WorkerState`, its current binding, its last Handoff, and its own `WorkerRuntime` plus the task
running it. `spawn_sub_bee` (`spawn.py`) is the whole start-up sequence: attenuate capabilities,
carve a grant slice, resolve the assignment's slot to a live model, build the Capping gate and
`WorkerContext`, start the `WorkerRuntime` and send the sub-bee its first `TaskAssign`;
`stop_sub_bee` (`spawn.py`) is its counterpart, stopping that runtime cooperatively before falling
back to a bounded cancel (codingrules section 11: never left cancelled-but-unawaited). Roadmap
step 10.3 (ADR-0039) adds `attenuate` (a sub-bee's capability slice, read off its assignment: the
goal's set and the task's network scopes narrow it) and `binding` (the `slot_binding` point every
binding passes, first or rebound, before anything is started).
`InCellSpawnSource` (`in_cell.py`, roadmap step 5.5) is a different kind of "spawn" decision: which
`RealCellSource` a Warden leases its own Cell through, so a Warden already running inside a Virtual
Cell opens an `InCellSession` on itself instead of the Hive Stand's `LocalProcessSession`, without
any code anywhere branching on `cell.kind`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles starting and stopping one Worker on the Warden's Cell, and which RealCellSource a
    Warden leases that Cell through. `spawn_sub_bee` is called by `hivemind.wardens.ticks.assign`
    and `hivemind.wardens.ticks.alarms`; `stop_sub_bee` is called by `hivemind.wardens.warden.
    Warden.stop`; `InCellSpawnSource` is built and injected as `WardenDeps.source` by
    `hivemind.cli.in_cell`, the in-Cell Warden's own composition root.

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

Public API (roadmap step 5.5):
    - InCellSpawnConfig, InCellSpawnSource: the `in_cell` strategy -- a RealCellSource for a
      Warden already running inside its own Virtual Cell, chosen by injecting it as
      `WardenDeps.source` rather than by branching on `cell.kind` (in_cell).

Public API (roadmap step 10.3):
    - sub_bee_capabilities, declared_write_roots: a sub-bee's capability slice (attenuate).
    - BindingCheck, authorize_binding, binding_check: the `slot_binding` point (binding, spawn).
"""

from hivemind.wardens.spawn.attenuate import declared_write_roots, sub_bee_capabilities
from hivemind.wardens.spawn.binding import BindingCheck, authorize_binding
from hivemind.wardens.spawn.in_cell import InCellSpawnConfig, InCellSpawnSource
from hivemind.wardens.spawn.spawn import (
    WardenCellContext,
    binding_check,
    spawn_sub_bee,
    stop_sub_bee,
)
from hivemind.wardens.spawn.sub_bee import SubBee

__all__ = [
    "BindingCheck",
    "InCellSpawnConfig",
    "InCellSpawnSource",
    "SubBee",
    "WardenCellContext",
    "authorize_binding",
    "binding_check",
    "declared_write_roots",
    "spawn_sub_bee",
    "stop_sub_bee",
    "sub_bee_capabilities",
]
