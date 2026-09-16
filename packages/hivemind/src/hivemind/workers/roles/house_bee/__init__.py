"""Implement HouseBee: the maintenance role that sweeps hot state and Bee Bread (roadmap step 4.3).

A House Bee keeps memory from growing without bound: its one duty, a **sweep**, first demotes
whatever `hivemind.memory.should_demote` says has left hot state's own window into Bee Bread (the
warm tier), expires every WRITTEN Cell Wax note (a Queen-written caution about one Cell) whose own
`expires_at` has passed (`hivemind.memory.cell_wax.expire_wax`, roadmap step 4.2a's own named
wax-expiry hook), then folds Bee Bread entries older than that same window, for closed tasks, into
new `SUMMARY` entries through `hivemind.memory.compact` -- never from a previous summary
(docs/adr/0022, "one level"). A fourth phase, ripening Bee Bread (and cleared/expired Cell Wax)
into Honey (the cold tier), is a named no-op hook until phase 7's Honey Store exists. This package
is split by responsibility (codingrules section
5.2): `sweep.py` holds `run_sweep` and the pure counts/bundles it works over
(`SweepDeps`/`SweepWindow`/`SweepOutcome`), decoupled from the Worker protocol so a future
timer-driven supervisor can call it directly; `schedule.py` holds `SweepSchedule`, the pure timer a
supervisor checks before doing so; `role.py` holds `HouseBee` itself, the `hivemind.workers.base.
Worker`-protocol adapter around one sweep. This face only re-exports (codingrules section 5.4).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Constructed by a Warden's
    spawn logic (roadmap step 3.19, a parallel dispatch) and run once per attempt by
    `hivemind.workers.runtime.WorkerRuntime`; `run_sweep` is also exposed directly so that same
    parallel dispatch can wire `SweepSchedule` onto the Queen's or a Warden's own timer without
    going through a `TaskAssign` at all. Calls into `hivemind.cell`, `hivemind.llm`,
    `hivemind.memory`, `hivemind.workers.base`, `hivemind.workers.context` and waggle only; never
    `hivemind.wardens` or `hivemind.queen` (codingrules section 4: "a Worker never imports its
    Warden").

Key invariants:
    - `HouseBee.run` never marks a task SUCCEEDED (codingrules section 8.7): every attempt returns
      `claimed=True, handoff=None`.
    - `run_sweep` never summarises a `BeeBreadEntryKind.SUMMARY` entry (docs/adr/0022's "one level
      of summary"), and never a compaction batch larger than `hivemind.memory.bee_bread.entry.
      MAX_REF_IDS`.

See Also:
    - .claude/codingrules.md section 8.7 for "a Worker never marks itself SUCCEEDED."
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency", extended here
      to compaction.
    - .claude/roadmap.md step 4.3 for the work that populates this package.
    - hivemind.workers.roles.drone for Drone, the sibling role this package's shape mirrors.
    - hivemind.memory.compact and hivemind.memory.demote for this package's two memory-side
      collaborators.

Public API (roadmap 4.3):
    - HouseBee, HOUSE_BEE_HOT_WINDOW_S: the role itself, and its mirrored hot-window constant
      (hivemind.workers.roles.house_bee.role).
    - run_sweep, SweepDeps, SweepWindow, SweepOutcome, SWEEP_NOTE_LIMIT, SWEEP_DECISION_LIMIT: one
      sweep's own work, decoupled from the Worker protocol (hivemind.workers.roles.house_bee.sweep).
    - SweepSchedule: the pure timer a supervisor checks before running a sweep
      (hivemind.workers.roles.house_bee.schedule).
"""

from hivemind.workers.roles.house_bee.role import HOUSE_BEE_HOT_WINDOW_S, HouseBee
from hivemind.workers.roles.house_bee.schedule import SweepSchedule
from hivemind.workers.roles.house_bee.sweep import (
    SWEEP_DECISION_LIMIT,
    SWEEP_NOTE_LIMIT,
    SweepDeps,
    SweepOutcome,
    SweepWindow,
    run_sweep,
)

__all__ = [
    "HOUSE_BEE_HOT_WINDOW_S",
    "SWEEP_DECISION_LIMIT",
    "SWEEP_NOTE_LIMIT",
    "HouseBee",
    "SweepDeps",
    "SweepOutcome",
    "SweepSchedule",
    "SweepWindow",
    "run_sweep",
]
