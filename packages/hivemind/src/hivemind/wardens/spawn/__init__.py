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
binding passes, first or rebound, before anything is started). Roadmap step 8.1 adds `route` (the
Warden's routing step: which binding of the slot's chain a sub-bee is bound to first, by tempo and
Forage).
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

Public API (roadmap step 8.1):
    - Routed, RouteScope, BindingTarget, route_rebind: the routing step (route); the rest of the
      module (`pick_binding`, `bind_routed`, `record_routing`) is `spawn_sub_bee`'s own.
Public API (roadmap step 9.7):
    - CombSeams, CombHandles, make_handles, visible_entries: a sub-bee's Comb tools (comb).

Public API (roadmap step 10.3):
    - sub_bee_capabilities, declared_write_roots, widen_lease_reachability: a sub-bee's capability
      slice and the lease widening its declared write roots need (attenuate).
    - BindingCheck, authorize_binding, binding_check: the `slot_binding` point (binding, spawn).

Public API (roadmap step 11.9a):
    - LocalRun (sub_bee), LocalAssignment, spawn_local (local): a sub-bee a Warden starts for a task
      it minted itself, whose ending stays on its row.
"""

from hivemind.wardens.spawn.attenuate import (
    declared_write_roots,
    sub_bee_capabilities,
    widen_lease_reachability,
)
from hivemind.wardens.spawn.binding import BindingCheck, authorize_binding
from hivemind.wardens.spawn.comb import CombHandles, CombSeams, make_handles, visible_entries
from hivemind.wardens.spawn.in_cell import InCellSpawnConfig, InCellSpawnSource
from hivemind.wardens.spawn.local import LocalAssignment, spawn_local
from hivemind.wardens.spawn.route import BindingTarget, Routed, RouteScope, route_rebind
from hivemind.wardens.spawn.spawn import (
    WardenCellContext,
    binding_check,
    spawn_sub_bee,
    stop_sub_bee,
)
from hivemind.wardens.spawn.sub_bee import LocalRun, SubBee

__all__ = [
    "BindingCheck",
    "BindingTarget",
    "CombHandles",
    "CombSeams",
    "InCellSpawnConfig",
    "InCellSpawnSource",
    "LocalAssignment",
    "LocalRun",
    "RouteScope",
    "Routed",
    "SubBee",
    "WardenCellContext",
    "authorize_binding",
    "binding_check",
    "declared_write_roots",
    "make_handles",
    "route_rebind",
    "spawn_local",
    "spawn_sub_bee",
    "stop_sub_bee",
    "sub_bee_capabilities",
    "visible_entries",
    "widen_lease_reachability",
]
