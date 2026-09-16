"""Hold one module per Worker role: the roles package.

Forager, Scout, GuardBee, Undertaker, Drone and HouseBee each implement the shared Worker
protocol. Roadmap step 3.16 added the first, `hivemind.workers.roles.drone.Drone`, a generic,
disposable role that works one task through a bounded tool loop. Roadmap step 4.3 adds the second,
`hivemind.workers.roles.house_bee.HouseBee`, a maintenance role that runs one sweep (demote, then
compact) over hot state and Bee Bread.

Fits into the Hive:
    Layer 4 (roles that do the work), inside the workers package. Handles one module (or package)
    per Worker role implementing the shared Worker protocol. Called by
    `hivemind.workers.runtime.WorkerRuntime` on behalf of whatever spawned it (a Warden, roadmap
    step 3.19); calls into `hivemind.workers.context`, `hivemind.workers.tools` and sibling
    packages at Layer 4 or below, never back up into `hivemind.workers`'s other sub-packages
    directly.

Key invariants:
    - Every role's `run` never marks a task SUCCEEDED (codingrules section 8.7): `WorkerOutcome.
      claimed` only says the role believes the work is done.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under workers.
    - .claude/roadmap.md phase 3 step 3.16 for the work that first populated it; step 4.3 for the
      second role.
    - hivemind.workers.roles.drone for Drone, this package's first role.
    - hivemind.workers.roles.house_bee for HouseBee, this package's second role.

Public API (roadmap 3.16, extended by 4.3):
    - Drone, DRONE_MAX_ROUNDS, HandoffRequestedError: the Drone role (hivemind.workers.roles.drone).
    - HouseBee, HOUSE_BEE_HOT_WINDOW_S, run_sweep, SweepDeps, SweepWindow, SweepOutcome,
      SweepSchedule, SWEEP_NOTE_LIMIT, SWEEP_DECISION_LIMIT: the HouseBee role
      (hivemind.workers.roles.house_bee).
"""

from hivemind.workers.roles.drone import DRONE_MAX_ROUNDS, Drone, HandoffRequestedError
from hivemind.workers.roles.house_bee import (
    HOUSE_BEE_HOT_WINDOW_S,
    SWEEP_DECISION_LIMIT,
    SWEEP_NOTE_LIMIT,
    HouseBee,
    SweepDeps,
    SweepOutcome,
    SweepSchedule,
    SweepWindow,
    run_sweep,
)

__all__ = [
    "DRONE_MAX_ROUNDS",
    "HOUSE_BEE_HOT_WINDOW_S",
    "SWEEP_DECISION_LIMIT",
    "SWEEP_NOTE_LIMIT",
    "Drone",
    "HandoffRequestedError",
    "HouseBee",
    "SweepDeps",
    "SweepOutcome",
    "SweepSchedule",
    "SweepWindow",
    "run_sweep",
]
