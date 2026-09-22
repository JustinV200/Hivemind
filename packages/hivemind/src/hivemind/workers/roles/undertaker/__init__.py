"""Undertaker: destroys Virtual Cells and releases Real Cell leases, idempotently, with retries.

Roadmap step 5.8. `role.py` defines `Undertaker` (the `Worker`-protocol adapter, autopilot-only --
it never awaits a model) and its two operations, `destroy_virtual`/`release_real`, plus the three
injected Protocols (`GrantRevoker`, `WaxRetirer`, `LeavingsRemover`) that keep it decoupled from
Layer 6 (`queen.forage`) and from another branch's not-yet-merged Leavings ledger. `sweep.py`
defines `sweep_orphans`, the Queen-startup sweep that finds orphaned Virtual Cells (from backend
labels), orphaned Real Cell leases (from the trail) and expired dormant Cells (from the
Overwintering pool), and cleans each up through an `Undertaker`. `schedule.py` defines
`UndertakerSweepSchedule`, the pure timer a future periodic sweep checks.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Calls into `hivemind.cell`,
    `hivemind.hive` (CellBackend, HiveError), `hivemind.memory`, `hivemind.pheromone`,
    `hivemind.workers.base`, `hivemind.workers.context` and waggle only.

Key invariants:
    - `Undertaker` never imports `hivemind.llm` (codingrules section 4: nothing under an
      `autopilot/`-shaped role awaits a model; this role's whole job needs no judgement call).
    - `destroy_virtual`/`release_real` are each idempotent end to end and each retried with
      exponential backoff around every effectful step.

See Also:
    - .claude/roadmap.md step 5.8 for this package's own build instructions.
    - .claude/codingrules.md section 8.7 for the CellKind branch `role.py` is one of two allowed
      callers of.
    - hivemind.workers.roles.house_bee for HouseBee, the autopilot-only shape this package mirrors.
    - hivemind.hive.lifecycle for CellLifecycle.evict_expired, sweep_orphans's dormant-eviction
      collaborator (it calls hivemind.hive.overwinter.pool.OverwinterPool.evict_expired itself,
      then tears each expired Cell down on the backend).

Public API:
    - Undertaker, UndertakerDeps, RetryPolicy, GrantRevoker, WaxRetirer, LeavingsRemover,
      NullLeavingsRemover, NullWaxRetirer, DEFAULT_MAX_ATTEMPTS, DEFAULT_INITIAL_BACKOFF_S,
      DEFAULT_BACKOFF_FACTOR, DEFAULT_MAX_BACKOFF_S: the role itself
      (hivemind.workers.roles.undertaker.role).
    - sweep_orphans, SweepDeps, SweepReport, orphan_virtual_cells, orphan_real_leases,
      KnownLiveCells, LeaseFinder, DormantEvictor: the Queen-startup sweep
      (hivemind.workers.roles.undertaker.sweep).
    - UndertakerSweepSchedule, DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S: the pure periodic-sweep timer
      (hivemind.workers.roles.undertaker.schedule).
"""

from hivemind.workers.roles.undertaker.role import (
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_INITIAL_BACKOFF_S,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_BACKOFF_S,
    GrantRevoker,
    LeavingsRemover,
    NullLeavingsRemover,
    NullWaxRetirer,
    RetryPolicy,
    Undertaker,
    UndertakerDeps,
    WaxRetirer,
)
from hivemind.workers.roles.undertaker.schedule import (
    DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S,
    UndertakerSweepSchedule,
)
from hivemind.workers.roles.undertaker.sweep import (
    DormantEvictor,
    KnownLiveCells,
    LeaseFinder,
    SweepDeps,
    SweepReport,
    orphan_real_leases,
    orphan_virtual_cells,
    sweep_orphans,
)

__all__ = [
    "DEFAULT_BACKOFF_FACTOR",
    "DEFAULT_INITIAL_BACKOFF_S",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF_S",
    "DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S",
    "DormantEvictor",
    "GrantRevoker",
    "KnownLiveCells",
    "LeaseFinder",
    "LeavingsRemover",
    "NullLeavingsRemover",
    "NullWaxRetirer",
    "RetryPolicy",
    "SweepDeps",
    "SweepReport",
    "Undertaker",
    "UndertakerDeps",
    "UndertakerSweepSchedule",
    "WaxRetirer",
    "orphan_real_leases",
    "orphan_virtual_cells",
    "sweep_orphans",
]
