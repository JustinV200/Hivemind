"""Define sweep_orphans: the Queen-startup sweep for orphaned Cells, leases and dormant Cells.

Roadmap step 5.8: "On Queen startup sweeps orphans of both kinds from backend labels and the
trail." Three kinds of orphan, found three different ways: a Virtual Cell the backend's own
infrastructure still holds (`hivemind.hive.backends.base.CellBackend.list_cells`) that no live
Cell table (a callable, so this module never imports `hivemind.hive.lifecycle`, a concurrent
dispatch) still recognises; a Real Cell lease the trail shows `cell.leased` for with no matching
`cell.released`, whose own task is no longer active in the Brood Chamber; and a dormant Cell past
its own `dormant_until` in the Overwintering pool. `orphan_virtual_cells` and `orphan_real_leases`
are the pure decision for the first two (codingrules section 8.3: "pure core, effectful edges"),
each independently testable with plain lists in, a tuple of ids out; `sweep_orphans` itself is the
effectful caller that queries the backend and the trail, calls the pure functions, and hands every
id found to `hivemind.workers.roles.undertaker.role.Undertaker`'s own idempotent operations. The
third kind (dormant eviction) has no pure half here at all: `hivemind.hive.overwinter.pool.
OverwinterPool.evict_expired` already is both the decision and the effect, so this module's own
dormant-eviction phase is one call, whose returned ids are folded straight into the report.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.undertaker`. Called once, by
    whichever composition root brings the Queen up (a concurrent dispatch), before she starts
    accepting new work. Calls into `hivemind.cell` (CellIdentity), `hivemind.hive.backends.base`
    (CellBackend, VirtualCellRecord), `hivemind.pheromone` (PheromoneEvent, PheromoneTrail,
    TrailQuery, MAX_QUERY_LIMIT), `hivemind.workers.roles.undertaker.role` (Undertaker) and waggle
    only.

Key invariants:
    - `orphan_virtual_cells`/`orphan_real_leases` perform no I/O and read no clock: same inputs in,
      same tuple of ids out, every time.
    - `sweep_orphans` never raises for a lease `lease_finder` cannot resolve: it is simply left out
      of `SweepReport.real_released`, since nothing further can be done for it this sweep (module
      docstring: only `orphan_real_leases`'s own pure decision is guaranteed complete; resolving an
      id to an actual `hivemind.cell.RealCellLease` object depends on what the caller's own
      `LeaseFinder` can still find).
    - `SweepReport` is frozen: one snapshot of what one sweep did, never mutated afterwards.

See Also:
    - .claude/roadmap.md step 5.8 for this module's spec verbatim ("On Queen startup sweeps
      orphans of both kinds from backend labels and the trail").
    - .claude/codingrules.md section 8.3 for "pure core, effectful edges".
    - hivemind.workers.roles.undertaker.role for Undertaker, destroy_virtual and release_real.
    - hivemind.hive.overwinter.pool for OverwinterPool.evict_expired, the dormant-eviction phase's
      own decision-and-effect.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from hivemind.cell import CellIdentity, RealCellLease
from hivemind.hive.backends.base import CellBackend, VirtualCellRecord
from hivemind.pheromone import (
    MAX_QUERY_LIMIT,
    CellEvent,
    PheromoneEvent,
    PheromoneTrail,
    TrailQuery,
)
from hivemind.workers.roles.undertaker.role import Undertaker
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, LeaseId, TaskId, new_event_id

# Injected so this module never imports hivemind.hive.lifecycle (a concurrent dispatch) or
# hivemind.brood_chamber's own store directly: the composition root that already holds both
# supplies these as plain callables instead. RealCellLease itself is a plain, legal downward
# import (hivemind.cell sits below hivemind.workers), so only the *lookup* is injected, not the
# type.
KnownLiveCells = Callable[[], Awaitable[frozenset[CellId]]]
LeaseFinder = Callable[[LeaseId], Awaitable[RealCellLease | None]]
DormantEvictor = Callable[[datetime], Awaitable[Sequence[CellId]]]

# cell.* is the nearest existing family for a whole-sweep summary; no dedicated "a sweep ran" kind
# exists yet (hivemind.queen.ticks.housekeeping's own House Bee sweep has the same gap, by its own
# module docstring). cell.destroyed is the closest fit: every branch of this sweep either destroys
# a Virtual Cell, releases a lease (also a teardown) or evicts an expired dormant Cell (a destroy
# too), so "something was torn down at Queen startup" is the summary's own meaning. A dedicated
# cell.orphans_swept kind would say this precisely (reported to the orchestrator).
_SWEEP_SUMMARY_KIND = "cell.destroyed"

__all__ = [
    "DormantEvictor",
    "KnownLiveCells",
    "LeaseFinder",
    "SweepDeps",
    "SweepReport",
    "orphan_real_leases",
    "orphan_virtual_cells",
    "sweep_orphans",
]


@dataclass(frozen=True, slots=True)
class SweepDeps:
    """Every collaborator one Queen-startup sweep needs, bundled per codingrules 5.1.

    Attributes:
        undertaker: Performs every destroy_virtual/release_real this sweep finds.
        backend: Lists what Virtual Cell infrastructure this Hive's backend currently holds.
        hive_id: Which Hive to sweep; passed straight to `backend.list_cells`.
        trail: Queried for `cell.leased`/`cell.released` events and written the sweep's own
            summary event.
        clock: Source of `evict_dormant`'s own `now` and the summary event's timestamp.
        identity: The Hive, node and actor this sweep stamps on its own summary event.
        known_live_cells: Returns every Cell id the live lifecycle table still recognises.
        lease_finder: Resolves an orphaned lease id to the actual lease object, or None.
        evict_dormant: Destroys every dormant Cell past its own `dormant_until`
            (`hivemind.hive.overwinter.pool.OverwinterPool.evict_expired`).
    """

    undertaker: Undertaker
    backend: CellBackend
    hive_id: HiveId
    trail: PheromoneTrail
    clock: Clock
    identity: CellIdentity
    known_live_cells: KnownLiveCells
    lease_finder: LeaseFinder
    evict_dormant: DormantEvictor


@dataclass(frozen=True, slots=True)
class SweepReport:
    """What one Queen-startup sweep found and cleaned up.

    Attributes:
        virtual_destroyed: Virtual Cell ids the backend held that the lifecycle table did not
            recognise, all now destroyed.
        real_released: Lease ids the trail showed leased-but-never-released, for a task no longer
            active, that `lease_finder` could resolve and that are now released.
        dormant_evicted: Dormant Cell ids past their own `dormant_until`, all now destroyed.
    """

    virtual_destroyed: tuple[CellId, ...]
    real_released: tuple[LeaseId, ...]
    dormant_evicted: tuple[CellId, ...]


def orphan_virtual_cells(
    records: Sequence[VirtualCellRecord], known_live: frozenset[CellId]
) -> tuple[CellId, ...]:
    """Pick every Virtual Cell the backend still holds that the live lifecycle table does not know.

    Pure (module docstring): no I/O, no clock.

    Args:
        records: Every Virtual Cell `hivemind.hive.backends.base.CellBackend.list_cells` currently
            reports for this Hive.
        known_live: Every Cell id the live lifecycle table still recognises.

    Returns:
        The `cell_id` of every record whose id is not in `known_live`, in `records`' own order.
    """
    return tuple(record.cell_id for record in records if record.cell_id not in known_live)


def orphan_real_leases(
    leased_events: Sequence[PheromoneEvent],
    released_events: Sequence[PheromoneEvent],
    active_task_ids: frozenset[TaskId],
) -> tuple[LeaseId, ...]:
    """Pick every lease the trail shows opened but never closed, for a task no longer active.

    Pure (module docstring): no I/O, no clock. Ids are compared as strings throughout, since a
    trail event's own payload may have already round-tripped through JSON (a store's own
    `query()`), which turns a waggle NewType id into a bare `str`.

    Args:
        leased_events: Every `cell.leased` event on the trail (any Hive, any node -- the caller
            filters with `TrailQuery` before this is called).
        released_events: Every `cell.released` event on the trail.
        active_task_ids: Ids of tasks still RUNNING, ASSIGNED, BLOCKED or PAUSED in the Brood
            Chamber; a lease for any other task is an orphan even with no matching release.

    Returns:
        The `lease_id` of every `cell.leased` event with no matching `cell.released` and whose own
        `task_id` is not in `active_task_ids`, in `leased_events`' own order.
    """
    released_ids = {str(event.payload.get("lease_id")) for event in released_events}
    active_ids = {str(task_id) for task_id in active_task_ids}
    orphans: list[LeaseId] = []
    for event in leased_events:
        lease_id = event.payload.get("lease_id")
        if lease_id is None or str(lease_id) in released_ids:
            continue  # Already released, or malformed: nothing orphaned here.
        task_id = event.payload.get("task_id")
        if task_id is not None and str(task_id) in active_ids:
            continue  # Still legitimately in use by a task the Brood Chamber calls active.
        orphans.append(LeaseId(str(lease_id)))
    return tuple(orphans)


async def sweep_orphans(deps: SweepDeps, active_task_ids: frozenset[TaskId]) -> SweepReport:
    """Find and clean up every orphaned Virtual Cell, Real Cell lease and expired dormant Cell.

    Effects only happen here, never in `orphan_virtual_cells`/`orphan_real_leases` (module
    docstring): this function queries the backend and the trail, decides with the pure functions,
    then calls `deps.undertaker`'s own idempotent operations for what it found.

    Args:
        deps: Every collaborator this sweep needs.
        active_task_ids: Ids of tasks still RUNNING, ASSIGNED, BLOCKED or PAUSED, for
            `orphan_real_leases`'s own decision.

    Returns:
        A SweepReport of every id this sweep found and cleaned up.
    """
    records = await deps.backend.list_cells(deps.hive_id)
    known_live = await deps.known_live_cells()
    virtual_orphans = orphan_virtual_cells(records, known_live)
    for cell_id in virtual_orphans:
        await deps.undertaker.destroy_virtual(cell_id)

    leased_events = await deps.trail.query(TrailQuery(kind="cell.leased", limit=MAX_QUERY_LIMIT))
    released_events = await deps.trail.query(
        TrailQuery(kind="cell.released", limit=MAX_QUERY_LIMIT)
    )
    lease_orphans = orphan_real_leases(leased_events, released_events, active_task_ids)
    real_released: list[LeaseId] = []
    for lease_id in lease_orphans:
        lease = await deps.lease_finder(lease_id)
        if lease is None:
            continue  # Nothing this sweep can act on; left for a future sweep or a human to find.
        await deps.undertaker.release_real(lease)
        real_released.append(lease_id)

    dormant_evicted = tuple(await deps.evict_dormant(deps.clock.now()))

    report = SweepReport(
        virtual_destroyed=virtual_orphans,
        real_released=tuple(real_released),
        dormant_evicted=dormant_evicted,
    )
    await _record_summary(deps, report)
    return report


async def _record_summary(deps: SweepDeps, report: SweepReport) -> None:
    """Record one summary trail event for this sweep (module docstring: nearest existing kind)."""
    event = CellEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=_SWEEP_SUMMARY_KIND,
        subject_id=deps.hive_id,
        payload={
            "virtual_destroyed": len(report.virtual_destroyed),
            "real_released": len(report.real_released),
            "dormant_evicted": len(report.dormant_evicted),
        },
    )
    await deps.trail.record(event)
