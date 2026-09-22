"""Define run_housekeeping: the Queen's own tick-time cluster check and House Bee sweep.

Roadmap step 4.3: "A House Bee sweep duty... runs demotion and compaction on a timer" -- the timer
being the Queen's own tick, not a dedicated Worker assignment (that would need a Cell, a lease and
a grant for maintenance that touches no task). `run_housekeeping` is the one function
`hivemind.queen.queen.Queen`'s tick calls in place of the bare `run_cluster_tick(...)` it used to
call directly (roadmap step 4.9's own wiring): it still runs `hivemind.queen.cluster.tick.
run_cluster_tick` first, unconditionally, then (roadmap step 5.13) `hivemind.queen.cluster.tick.
run_release_tick` (drains any pending `hive cells release` order the same tick), then checks
`hivemind.workers.roles.house_bee.SweepSchedule.is_due` against `deps.housekeeping.last_sweep_at`
and, when due, runs one House Bee
sweep (`hivemind.workers.roles.house_bee.run_sweep`) directly over the Queen's own memory store --
never through a `TaskAssign` to a Warden, since the Queen has no Cell of her own to spawn a Worker
on. A House Bee (a maintenance role) sweep is: demote whatever has aged out of hot state into Bee
Bread (the warm memory tier), expire Cell Wax past its own deadline, then fold old Bee Bread entries
for closed tasks into one summary on `ModelSlot.RIPENER`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called once per tick by `hivemind.queen.queen.Queen`'s own `_run_tick`, in place
    of `run_cluster_tick`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.memory`
    (BeeBread, MemoryContext), `hivemind.queen.cluster.tick` (run_cluster_tick, run_release_tick),
    `hivemind.queen.deps` (QueenDeps, WardenLink, Housekeeping, under TYPE_CHECKING),
    `hivemind.queen.state` (ClusterState), `hivemind.workers.roles.house_bee` (SweepDeps,
    SweepSchedule, SweepWindow, run_sweep) and waggle only.

Key invariants:
    - `run_housekeeping` never awaits a model on its own `run_cluster_tick` half (that module's own
      "Key invariants"); the sweep half awaits exactly one model call at most, on `ModelSlot.
      RIPENER`, only when `SweepSchedule.is_due` says a sweep is due this tick.
    - `deps.housekeeping.last_sweep_at` only ever advances to `deps.clock.now()`, and only once a
      sweep has actually run to completion -- a sweep that raises leaves it unchanged, so the next
      tick tries again rather than silently skipping a whole interval. The Queen's very first tick
      only seeds `last_sweep_at` (never runs a sweep immediately): `SweepSchedule.is_due(None,
      now)` is always True by design (a House-Bee-run sweep's own first-run rule), which would
      otherwise make a fresh Hive's first-ever tick pay for a sweep before anything has had a
      chance to accumulate; the first real sweep is due one `sweep_interval_s` later instead.
    - `run_housekeeping` never lets a missing `ModelSlot.RIPENER` binding raise out of the Queen's
      own tick: `_resolve_ripener` catches the two shapes a caller's own `bound_for` can fail with
      (`UnresolvableSlotError` from a real `hivemind.llm.slots.resolve`; `KeyError` from a test
      fixture's own plain-dict lookup) and returns `None` instead, so `hivemind.workers.roles.
      house_bee.run_sweep` still demotes and expires Cell Wax -- only compaction, the one phase
      that needs a model, is skipped for that sweep (`hivemind.workers.roles.house_bee.SweepDeps.
      bound`'s own docstring). Logged once (`deps.housekeeping.ripener_unbound_warned`), not every
      tick, so an operator who never configured `[llm.slots.ripener]` is told, but not spammed.
    - The sweep's own per-item events (`memory.demoted`, `memory.wax_expired`, `memory.compacted`)
      are the record of what one sweep did; no additional `memory.*` event exists for "a sweep ran"
      itself (roadmap step 4.3's own kind table names none, and `hivemind.pheromone.events.
      families` is outside this dispatch's own file list to add one to).

See Also:
    - .claude/roadmap.md step 4.3 for "a House Bee sweep duty... runs demotion and compaction on a
      timer".
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency".
    - hivemind.workers.roles.house_bee for run_sweep, SweepDeps, SweepWindow and SweepSchedule,
      this module's one maintenance collaborator.
    - hivemind.queen.cluster.tick for run_cluster_tick, this module's other half.
    - hivemind.queen.deps for Housekeeping, the mutable `last_sweep_at`/`ripener_unbound_warned`
      bookkeeping this module reads and advances.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.forage.slots import ModelSlot
from hivemind.llm import BoundModel, UnresolvableSlotError
from hivemind.memory import BeeBread, MemoryContext
from hivemind.queen.cluster.tick import run_cluster_tick, run_release_tick
from hivemind.queen.state import ClusterState
from hivemind.workers.roles.house_bee import SweepDeps, SweepSchedule, SweepWindow, run_sweep

if TYPE_CHECKING:
    # Only for the type hints below: every hivemind.queen.ticks module keeps its own QueenDeps/
    # WardenLink import TYPE_CHECKING-only, since hivemind.queen.deps.QueenDeps.housekeeping is
    # typed as this package's own Housekeeping (queen/deps.py, defined there rather than here so a
    # real import of it never has to run this whole ticks package's own __init__ first).
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["run_housekeeping"]

# The allowance a Queen-run sweep reads and writes at: the Queen's own memory store holds nothing
# above C2 (hivemind.queen.awake's own episodes are the highest-clearance writer), so this is
# generous enough to sweep everything a Queen-run House Bee could ever see.
_SWEEP_ALLOWANCE = HoneyClearance.C2
log = get_logger(__name__)


async def run_housekeeping(
    deps: QueenDeps, wardens: Sequence[WardenLink], state: ClusterState
) -> None:
    """Run one Clustering check, then a House Bee sweep if the manifest's own interval is due.

    Args:
        deps: The Queen's collaborators; `housekeeping`, `sweep_interval_s`, `hot_window_s`,
            `memory`, `identity`, `clock`, `bound_for` and `call_gate` are this call's own inputs.
        wardens: Every Warden currently attached; passed straight through to `run_cluster_tick`.
        state: The Queen's own mode and clustered-provider set; mutated by `run_cluster_tick`.
    """
    await run_cluster_tick(deps, state, wardens)
    # Roadmap step 5.13: drains RELEASE orders (`hive cells release`) the same tick, separately
    # from CLUSTER/WAKE -- see hivemind.queen.cluster.tick's own module docstring for why this is
    # a second call rather than one more branch inside run_cluster_tick's own order drain.
    await run_release_tick(deps, wardens)
    now = deps.clock.now()
    if deps.housekeeping.last_sweep_at is None:
        # First tick ever: seed the timer instead of sweeping immediately (module docstring's own
        # "Key invariants" -- SweepSchedule.is_due(None, now) would otherwise always fire here).
        deps.housekeeping.last_sweep_at = now
        return
    schedule = SweepSchedule(interval_s=deps.sweep_interval_s)
    if not schedule.is_due(deps.housekeeping.last_sweep_at, now):
        return  # Not due yet; the next tick checks again.
    await run_sweep(_sweep_deps(deps), _sweep_window(deps, now))
    # Advanced only once the sweep has actually finished (module docstring's own "Key invariants"):
    # a raised exception here leaves last_sweep_at untouched, so the next tick retries.
    deps.housekeeping.last_sweep_at = now


def _sweep_deps(deps: QueenDeps) -> SweepDeps:
    """Build the SweepDeps a Queen-run sweep uses: her own memory store, on ModelSlot.RIPENER."""
    bound = _resolve_ripener(deps)
    return SweepDeps(
        memory=MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock),
        bee_bread=BeeBread(deps.memory),
        bound=bound,
        gate=deps.call_gate if bound is not None else None,
    )


def _resolve_ripener(deps: QueenDeps) -> BoundModel | None:
    """Resolve `ModelSlot.RIPENER` to a BoundModel, or None (and log once) when it is unbound.

    Args:
        deps: The Queen's collaborators; `bound_for` is this call's own input.

    Returns:
        The RIPENER binding, or `None` when `deps.bound_for` has nothing configured for it
        (module docstring's own "Key invariants": a missing binding skips compaction, not the
        whole sweep).
    """
    try:
        return deps.bound_for(ModelSlot.RIPENER)
    except (UnresolvableSlotError, KeyError):
        if not deps.housekeeping.ripener_unbound_warned:
            # Logged once (module docstring): an operator who never configured [llm.slots.ripener]
            # is told, but a sweep this due-but-unbound never spams it on every later tick.
            log.warning("queen.sweep_skipped_compaction_no_ripener_binding")
            deps.housekeeping.ripener_unbound_warned = True
        return None


def _sweep_window(deps: QueenDeps, now: datetime) -> SweepWindow:
    """Build the SweepWindow a Queen-run sweep measures age against, from `deps.hot_window_s`."""
    return SweepWindow(
        now=now, hot_window=timedelta(seconds=deps.hot_window_s), allowance=_SWEEP_ALLOWANCE
    )
