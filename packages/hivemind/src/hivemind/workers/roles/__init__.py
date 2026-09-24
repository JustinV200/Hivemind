"""Hold one module per Worker role: the roles package.

Forager, Scout, GuardBee, Undertaker, Drone and HouseBee each implement the shared Worker
protocol. Roadmap step 3.16 added the first, `hivemind.workers.roles.drone.Drone`, a generic,
disposable role that works one task through a bounded tool loop. Roadmap step 4.3 adds the second,
`hivemind.workers.roles.house_bee.HouseBee`, a maintenance role that runs one sweep (demote, then
compact) over hot state and Bee Bread. Roadmap step 5.8 adds the third,
`hivemind.workers.roles.undertaker.Undertaker`, the cleanup role that destroys Virtual Cells and
releases Real Cell leases, idempotently, with retries, and the Queen-startup sweep that finds
orphans of both kinds.

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
      second role; step 5.8 for the third.
    - hivemind.workers.roles.drone for Drone, this package's first role.
    - hivemind.workers.roles.house_bee for HouseBee, this package's second role.
    - hivemind.workers.roles.undertaker for Undertaker, this package's third role.

Public API (roadmap 3.16, extended by 4.3 and 5.8):
    - Drone, DRONE_MAX_ROUNDS, HandoffRequestedError: the Drone role (hivemind.workers.roles.drone).
    - HouseBee, HOUSE_BEE_HOT_WINDOW_S, run_sweep, SweepDeps, SweepWindow, SweepOutcome,
      SweepSchedule, SWEEP_NOTE_LIMIT, SWEEP_DECISION_LIMIT: the HouseBee role
      (hivemind.workers.roles.house_bee).
    - Undertaker, UndertakerDeps, RetryPolicy, GrantRevoker, WaxRetirer, LeavingsRemover,
      NullLeavingsRemover, sweep_orphans, UndertakerSweepDeps, UndertakerSweepReport,
      orphan_virtual_cells, orphan_real_leases, UndertakerSweepSchedule: the Undertaker role
      (hivemind.workers.roles.undertaker). Re-exported here under an `Undertaker`-prefixed alias
      for `SweepDeps`/`SweepReport` (`UndertakerSweepDeps`/`UndertakerSweepReport`), since HouseBee
      already owns the bare `SweepDeps` name at this package's own face; import from
      `hivemind.workers.roles.undertaker` directly for the unprefixed names.
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
from hivemind.workers.roles.undertaker import (
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_INITIAL_BACKOFF_S,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_BACKOFF_S,
    DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S,
    GrantRevoker,
    LeavingsRemover,
    LeavingsStoreRemover,
    NullLeavingsRemover,
    RetryPolicy,
    Undertaker,
    UndertakerDeps,
    UndertakerSweepSchedule,
    WaxRetirer,
    orphan_real_leases,
    orphan_virtual_cells,
    sweep_orphans,
)
from hivemind.workers.roles.undertaker import DormantEvictor as UndertakerDormantEvictor
from hivemind.workers.roles.undertaker import KnownLiveCells as UndertakerKnownLiveCells
from hivemind.workers.roles.undertaker import LeaseFinder as UndertakerLeaseFinder
from hivemind.workers.roles.undertaker import SweepDeps as UndertakerSweepDeps
from hivemind.workers.roles.undertaker import SweepReport as UndertakerSweepReport

__all__ = [
    "DEFAULT_BACKOFF_FACTOR",
    "DEFAULT_INITIAL_BACKOFF_S",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF_S",
    "DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S",
    "DRONE_MAX_ROUNDS",
    "HOUSE_BEE_HOT_WINDOW_S",
    "SWEEP_DECISION_LIMIT",
    "SWEEP_NOTE_LIMIT",
    "Drone",
    "GrantRevoker",
    "HandoffRequestedError",
    "HouseBee",
    "LeavingsRemover",
    "LeavingsStoreRemover",
    "NullLeavingsRemover",
    "RetryPolicy",
    "SweepDeps",
    "SweepOutcome",
    "SweepSchedule",
    "SweepWindow",
    "Undertaker",
    "UndertakerDeps",
    "UndertakerDormantEvictor",
    "UndertakerKnownLiveCells",
    "UndertakerLeaseFinder",
    "UndertakerSweepDeps",
    "UndertakerSweepReport",
    "UndertakerSweepSchedule",
    "WaxRetirer",
    "orphan_real_leases",
    "orphan_virtual_cells",
    "run_sweep",
    "sweep_orphans",
]
