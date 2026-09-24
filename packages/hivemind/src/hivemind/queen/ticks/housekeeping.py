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
for closed tasks into one summary on `ModelSlot.RIPENER`. With a Honey Store wired (`QueenDeps.
honey`, roadmap phase 7) the sweep then deposits aged Bee Bread and retired Cell Wax into it as
Nectar, attributed to the Cells the Queen's own records name (`QueenCellRecords`: a task's Cell
from the Brood Chamber, or for a finished task the chamber's own `task.assigned` record on the
trail, described by her attached Warden links; task-less material to the Hive Stand), and
every tick drops chunked Waggle deposits abandoned past the spec's idle timeout
(`NectarIntake.expire_groups`), so an unfinished deposit never holds memory forever. Ripening
itself, which calls models, never runs here: the House Bee's own loop does it beside the Queen.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called once per tick by `hivemind.queen.queen.Queen`'s own `_run_tick`, in place
    of `run_cluster_tick`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.memory`
    (BeeBread, MemoryContext), `hivemind.queen.cluster.tick` (run_cluster_tick, run_release_tick),
    `hivemind.queen.deps` (QueenDeps, WardenLink, Housekeeping, under TYPE_CHECKING),
    `hivemind.queen.state` (ClusterState), `hivemind.brood_chamber` (BroodChamber,
    TaskNotFoundError), `hivemind.pheromone` (the chamber's own placement records),
    `hivemind.workers.roles.house_bee` (SweepDeps, SweepSchedule, SweepWindow, run_sweep,
    HouseBeeHoney, GatheredOn) and waggle only.

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
    - The Honey half never awaits a model and never raises out of the tick: deposits are local
      SQLite through intake, and the House Bee's deposit duties log and skip what fails.
    - A Cell the Queen no longer has a link to is attributed conservatively (borrowed, so C2), and
      task-less material is attributed to the Hive Stand, identified the way placement already
      identifies it (`Cell.source`), or skipped when no Hive Stand is attached.

See Also:
    - .claude/roadmap.md step 4.3 for "a House Bee sweep duty... runs demotion and compaction on a
      timer".
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency".
    - hivemind.workers.roles.house_bee for run_sweep, SweepDeps, SweepWindow and SweepSchedule,
      this module's one maintenance collaborator.
    - hivemind.queen.cluster.tick for run_cluster_tick, this module's other half.
    - hivemind.queen.deps for Housekeeping, the mutable `last_sweep_at`/`ripener_unbound_warned`
      bookkeeping this module reads and advances.
    - hivemind.workers.roles.house_bee.honey for the two deposit duties the sweep's Honey half
      runs, and the CellRecords protocol `QueenCellRecords` implements.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from hivemind.brood_chamber import BroodChamber, TaskNotFoundError
from hivemind.cell import HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.forage.slots import ModelSlot
from hivemind.llm import BoundModel, UnresolvableSlotError
from hivemind.memory import BeeBread, MemoryContext
from hivemind.pheromone import PheromoneTrail, TrailQuery
from hivemind.queen.cluster.tick import run_cluster_tick, run_release_tick
from hivemind.queen.state import ClusterState
from hivemind.workers.roles.house_bee import (
    GatheredOn,
    HouseBeeHoney,
    SweepDeps,
    SweepSchedule,
    SweepWindow,
    run_sweep,
)
from waggle.ids import CellId, TaskId

if TYPE_CHECKING:
    # Only for the type hints below: every hivemind.queen.ticks module keeps its own QueenDeps/
    # WardenLink import TYPE_CHECKING-only, since hivemind.queen.deps.QueenDeps.housekeeping is
    # typed as this package's own Housekeeping (queen/deps.py, defined there rather than here so a
    # real import of it never has to run this whole ticks package's own __init__ first).
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = [
    "ASSIGNED_KIND",
    "HIVE_STAND_SOURCE",
    "QueenCellRecords",
    "placed_cell",
    "run_housekeeping",
]

# The allowance a Queen-run sweep reads and writes at: the Queen's own memory store holds nothing
# above C2 (hivemind.queen.awake's own episodes are the highest-clearance writer), so this is
# generous enough to sweep everything a Queen-run House Bee could ever see.
_SWEEP_ALLOWANCE = HoneyClearance.C2
# The `Cell.source` the Hive Stand's own Cell carries (hivemind.cell.local); the same test
# hivemind.queen.dispatcher.snapshot uses to tell placement which candidate is the Hive Stand.
HIVE_STAND_SOURCE = "hive_stand"
ASSIGNED_KIND = "task.assigned"  # The chamber's own event for a placement; it names the Cell.
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
    if deps.honey is not None:
        # Every tick, not only when a sweep is due: in memory and cheap, and it is what bounds how
        # long an abandoned chunked deposit can hold its buffered bytes.
        deps.honey.intake.expire_groups(now)
    if deps.housekeeping.last_sweep_at is None:
        # First tick ever: seed the timer instead of sweeping immediately (module docstring's own
        # "Key invariants" -- SweepSchedule.is_due(None, now) would otherwise always fire here).
        deps.housekeeping.last_sweep_at = now
        return
    schedule = SweepSchedule(interval_s=deps.sweep_interval_s)
    if not schedule.is_due(deps.housekeeping.last_sweep_at, now):
        return  # Not due yet; the next tick checks again.
    await run_sweep(_sweep_deps(deps, wardens), _sweep_window(deps, now))
    # Advanced only once the sweep has actually finished (module docstring's own "Key invariants"):
    # a raised exception here leaves last_sweep_at untouched, so the next tick retries.
    deps.housekeeping.last_sweep_at = now


def _sweep_deps(deps: QueenDeps, wardens: Sequence[WardenLink]) -> SweepDeps:
    """Build the SweepDeps a Queen-run sweep uses: her memory, RIPENER, and her Honey Store."""
    bound = _resolve_ripener(deps)
    # Honey only when a store is wired; the Cell records are rebuilt each sweep from the links
    # attached right now, so a Warden attached or lost since the last sweep is seen as it is.
    honey = (
        HouseBeeHoney(access=deps.honey, cells=QueenCellRecords(deps.chamber, deps.trail, wardens))
        if deps.honey is not None
        else None
    )
    return SweepDeps(
        memory=MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock),
        bee_bread=BeeBread(deps.memory),
        bound=bound,
        gate=deps.call_gate if bound is not None else None,
        honey=honey,
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


class QueenCellRecords:
    """The Queen's own answer to "which Cell was this gathered on?", for the House Bee's deposits.

    Implements `hivemind.workers.roles.house_bee.CellRecords` over what only the Queen knows: the
    Brood Chamber (and its own trail record of each placement) says which Cell a task ran on, and
    her attached Warden links carry each Cell's live record (borrowed or not, its tier). A fresh
    one is built for every sweep; it owns one mutable cache, of each task's placement, for that
    sweep only, since one task's Bee Bread entries all ask the same question.
    """

    def __init__(
        self, chamber: BroodChamber, trail: PheromoneTrail, wardens: Sequence[WardenLink]
    ) -> None:
        """Wrap the Queen's task store, her trail and the Warden links attached right now.

        Args:
            chamber: Where a task's live placement (`Task.cell_id`) is read.
            trail: Where a finished task's placement is read back (`placed_cell`).
            wardens: Every attached Warden link; each one's Cell is a live record.
        """
        self._chamber = chamber
        self._trail = trail
        self._wardens = tuple(wardens)
        self._placements: dict[TaskId, CellId | None] = {}  # This sweep's lookups, per task.

    async def gathered_on(self, task_id: TaskId | None) -> GatheredOn | None:
        """Return the Cell a task ran on, or the Hive Stand for task-less material.

        Args:
            task_id: The task the material concerns; None for the Queen's own task-less material
                (a demoted note, a Queen-level checkpoint), all of it written on the Hive Stand.

        Returns:
            The task's Cell (`for_cell`), or the Hive Stand when the task is unknown or was never
            placed; None when no Hive Stand is attached either (the deposit is skipped).
        """
        if task_id is None:
            return self._hive_stand()
        if task_id not in self._placements:
            self._placements[task_id] = await placed_cell(self._chamber, self._trail, task_id)
        cell_id = self._placements[task_id]
        # Never placed (or unknown here): whatever it left was written on the Hive Stand.
        return self.for_cell(cell_id) if cell_id is not None else self._hive_stand()

    def for_cell(self, cell_id: CellId) -> GatheredOn:
        """Describe one Cell from its live link, or conservatively when none is attached.

        Args:
            cell_id: The Cell the material names.

        Returns:
            `GatheredOn.of` the attached Cell's record, else `GatheredOn.unrecorded(cell_id)`.
        """
        link = next((link for link in self._wardens if link.cell.id == cell_id), None)
        return GatheredOn.of(link.cell) if link is not None else GatheredOn.unrecorded(cell_id)

    def _hive_stand(self) -> GatheredOn | None:
        """Return the attached Hive Stand's own Cell, or None when none is attached."""
        stand = next(
            (link.cell for link in self._wardens if link.cell.source == HIVE_STAND_SOURCE), None
        )
        return GatheredOn.of(stand) if stand is not None else None


async def placed_cell(
    chamber: BroodChamber, trail: PheromoneTrail, task_id: TaskId
) -> CellId | None:
    """Return the Cell a task was last placed on, even after finishing cleared its placement.

    The Brood Chamber clears `Task.cell_id` when a task reaches a terminal status, but its own
    `task.assigned` event, written in the same transaction as the placement, keeps the Cell's id;
    the latest such event is where the task last ran.

    Args:
        chamber: The Brood Chamber; a task still placed answers from its live record.
        trail: The Pheromone Trail the chamber records its transitions to.
        task_id: The task to locate.

    Returns:
        The Cell's id, or None when the task is unknown or was never placed.
    """
    try:
        # Local SQLite (or memory), milliseconds.
        task = await chamber.get(task_id)
    except TaskNotFoundError:
        return None  # Another Hive's id, or a purged row: no placement to read.
    if task.cell_id is not None:
        return task.cell_id  # Still placed: the live record is the answer.
    # Local SQLite, one indexed query by kind and subject; the newest assignment wins.
    assigned = await trail.query(TrailQuery(kind=ASSIGNED_KIND, subject_id=task_id))
    for event in reversed(assigned):
        cell_id = event.payload.get("cell_id")
        if isinstance(cell_id, str):
            return CellId(cell_id)
    return None
