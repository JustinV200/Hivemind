"""Fold the trail's lease events into the Hive Stand leases still open, and give orphans back.

The Hive Stand's lease lives in its Warden, and its Warden lives in the Queen's process: when that
process dies its lease dies unreleased, its scratch left on disk and no `cell.released` on the
trail (ADR-0062). The trail is the source of truth for which leases are still open: every
`cell.leased` adds one, every `cell.released` removes it. `fold_leases` is that rule, pure, over a
starting set (the snapshot's) and the events since; `lease_events` reads those events, bounded
by a cursor, from the Queen's own node only; `release_orphans` hands every open lease whose
holder is not attached to the Hive Stand's releaser, with the number of paths it touched outside
scratch (none of which can be restored now: their records died with the process).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. Called by `.recover` (the first half) and `.snapshot` (the fold).
    Calls into `hivemind.cell` (OrphanedLease), `hivemind.pheromone` (TrailQuery),
    `hivemind.queen.requeening.models` and waggle only.

Key invariants:
    - Folding is idempotent: reading an event twice (the snapshot's cursor overlaps) leaves the
      same set, because events are applied in trail order and a release is applied after its
      lease.
    - Only events the Queen's own node recorded about the Hive Stand's own Cell are read: a
      merged segment from any other node can never name a lease to give back here.
    - A lease is given back only when its holder is not attached to her now.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md.
    - hivemind.cell.local.source.HiveStandSource.release_orphan for what giving back does.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from hivemind.cell import OrphanedLease
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from hivemind.queen.requeening.models import MAX_OPEN_LEASES, OpenLease
from waggle.ids import CellId, WardenId

if TYPE_CHECKING:
    # Types only: `hivemind.queen.deps` imports this sub-package (`QueenDeps.requeening`).
    from hivemind.queen.deps import QueenDeps

LEASED_KIND = "cell.leased"  # A lease opened: payload names the lease, its holder and task.
RELEASED_KIND = "cell.released"  # A lease given back: payload names the lease.
TOUCHED_KIND = "cell.touched_outside_scratch"  # A path a lease wrote outside its scratch.

__all__ = [
    "LEASED_KIND",
    "RELEASED_KIND",
    "TOUCHED_KIND",
    "HiveStandLeases",
    "fold_leases",
    "lease_events",
    "release_orphans",
]


class HiveStandLeases(Protocol):
    """Give back a Hive Stand lease a dead process left open (`HiveStandSource` satisfies it)."""

    async def release_orphan(self, orphan: OrphanedLease) -> bool:
        """Remove the lease's scratch and record its `cell.released`; True once it is gone.

        Args:
            orphan: The lease, as the trail shows it.
        """
        ...


def fold_leases(
    start: Iterable[OpenLease], events: Iterable[PheromoneEvent]
) -> tuple[OpenLease, ...]:
    """Apply lease events, in trail order, to the leases open at the start.

    Args:
        start: The leases open where `events` begin (a snapshot's), or none.
        events: `cell.leased` and `cell.released` events about one Cell; any order.

    Returns:
        The leases still open after every event, oldest first, at most `MAX_OPEN_LEASES` (the
        newest kept: a fold that large is a corrupt trail, and the newest is what a live Warden
        could still be holding).
    """
    open_by_id = {lease.lease_id: lease for lease in start}
    # Trail order: a lease's release is always after its opening, so it is applied after it.
    for event in sorted(events, key=lambda event: (event.at, event.id)):
        lease_id = event.payload.get("lease_id")
        if not isinstance(lease_id, str):
            continue  # A malformed row names no lease; nothing to add or remove.
        if event.kind == RELEASED_KIND:
            open_by_id.pop(lease_id, None)
        elif event.kind == LEASED_KIND:
            open_by_id[lease_id] = _open_lease(event, lease_id)
    ordered = sorted(open_by_id.values(), key=lambda lease: (lease.leased_at, lease.lease_id))
    return tuple(ordered[-MAX_OPEN_LEASES:])


async def lease_events(
    deps: QueenDeps, cell_id: CellId, since: datetime | None
) -> tuple[PheromoneEvent, ...]:
    """Read every `cell.leased` and `cell.released` her own node recorded about `cell_id`.

    Args:
        deps: The Queen's collaborators; `trail` is read, `identity.node_id` filters it.
        cell_id: The Hive Stand's own Cell.
        since: Read from this instant on (inclusive); None reads the whole trail.

    Returns:
        The events, in no particular order (`fold_leases` orders them).
    """
    found: list[PheromoneEvent] = []
    for kind in (LEASED_KIND, RELEASED_KIND):
        found.extend(await _all_since(deps, kind, cell_id, since))
    return tuple(found)


async def release_orphans(
    deps: QueenDeps,
    leases: Sequence[OpenLease],
    attached: Collection[WardenId],
    releaser: HiveStandLeases,
) -> int:
    """Give back every open lease whose holder is not attached to her now.

    Args:
        deps: The Queen's collaborators; `trail` is read for each lease's touched paths.
        leases: The Hive Stand leases still open, from `fold_leases`.
        attached: Every Warden attached now; a lease one of them holds is that Warden's own.
        releaser: The Hive Stand's own source.

    Returns:
        How many leases were given back with their scratch gone.
    """
    released = 0
    for lease in leases:
        if lease.holder in attached:
            continue  # A live Warden's own lease: it releases it itself.
        touched = await _touched_outside(deps, lease)
        orphan = OrphanedLease(
            lease_id=lease.lease_id,
            holder=lease.holder,
            task_id=lease.task_id,
            touched_outside=touched,
        )
        # Latency: removing one scratch tree on this machine, off the loop, and one trail write.
        if await releaser.release_orphan(orphan):
            released += 1
    return released


def _open_lease(event: PheromoneEvent, lease_id: str) -> OpenLease:
    """Build the OpenLease one `cell.leased` describes."""
    holder, task_id = event.payload.get("holder"), event.payload.get("task_id")
    return OpenLease(
        lease_id=lease_id,
        cell_id=event.subject_id,
        holder=str(holder) if holder is not None else event.actor,
        task_id=str(task_id) if task_id is not None else None,
        leased_at=event.at,
    )


async def _all_since(
    deps: QueenDeps, kind: str, cell_id: CellId, since: datetime | None
) -> list[PheromoneEvent]:
    """Read every `kind` event about `cell_id` from her node since `since`, page by page."""
    found: list[PheromoneEvent] = []
    seen: set[str] = set()
    # Page by instant: each page starts where the last ended (inclusive), so an event at the
    # boundary is read twice and kept once; a page with nothing new ends it either way.
    while True:
        query = TrailQuery(
            kind=kind,
            subject_id=cell_id,
            node_id=deps.identity.node_id,
            since=since,
            limit=MAX_QUERY_LIMIT,
        )
        # Latency: one indexed local read of the trail per page.
        page = await deps.trail.query(query)
        fresh = [event for event in page if event.id not in seen]
        found.extend(fresh)
        seen.update(event.id for event in fresh)
        if len(page) < MAX_QUERY_LIMIT or not fresh:
            return found
        since = page[-1].at


async def _touched_outside(deps: QueenDeps, lease: OpenLease) -> int:
    """Count the paths `lease` touched outside its scratch, from her own trail."""
    events = await _all_since(deps, TOUCHED_KIND, CellId(lease.cell_id), lease.leased_at)
    return len({event.payload.get("path") for event in events if _names(event, lease)})


def _names(event: PheromoneEvent, lease: OpenLease) -> bool:
    """Whether `event` is about `lease`."""
    return event.payload.get("lease_id") == lease.lease_id
