"""Read one Cell's page: the Cell, its Warden and bees, work, Forage, Cell Wax, Alarms and Capping.

A Cell page (roadmap 12.4) joins the census (the Cell and its Warden), the telemetry board (the
bees and their context gauges), the Brood Chamber (the tasks placed on it), the Forage ledger (the
Warden's grants) and the trail (the requests still waiting, whether an Exoskeleton is attached, the
open Alarms and the Capping events, and when its Warden last ran a Patrol), plus memory's Cell Wax
and the hosting plan. This module reads each for the one Cell and hands them to the pure
``cell_page``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.cell_page``. Calls into the census, the shared gathers,
    the Capping read and the pure builder.

Key invariants:
    - Only reads; an unknown Cell raises ``CellNotFoundError`` (the census does).

See Also:
    - hivemind.observation.api.cell_page for the page this builds.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.entrance.gate.reads import HiveReads
from hivemind.entrance.reads.census import cell_view, warden_views
from hivemind.entrance.reads.screens.capping import read_capping
from hivemind.entrance.reads.screens.gather import (
    alarm_views,
    hosting_for,
    lease_starts,
    telemetry_of,
    unfinished_tasks,
    wax_notes,
)
from hivemind.memory import WaxState
from hivemind.observation import CellPageInputs, CellPageView, cell_page, grant_view
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.ids import CellId

FORAGE_EVENTS_PER_GRANT = 5  # Newest forage.* events read per grant: its last request and answer.
_EXOSKELETON_KINDS = ("cell.exoskeleton_attached", "cell.exoskeleton_detached")
_PATROL_KIND = "warden.patrol"  # Recorded by a watching Warden after each Patrol (ADR-0054).

__all__ = ["FORAGE_EVENTS_PER_GRANT", "read_cell_page"]


async def read_cell_page(reads: HiveReads, cell_id: CellId, now: datetime) -> CellPageView:
    """Read one Cell's page.

    Args:
        reads: The stores and live tables the Entrance reads.
        cell_id: The Cell.
        now: The instant the page is built for.

    Returns:
        The page.

    Raises:
        CellNotFoundError: No attached Warden supervises it and no lifecycle tracks it.
    """
    cell = await cell_view(reads, cell_id)
    wardens = {warden.id: warden for warden in warden_views(reads)}
    warden = wardens.get(cell.warden_id) if cell.warden_id is not None else None
    tasks = await unfinished_tasks(reads)
    grants = reads.ledger.grants_for(cell.warden_id) if cell.warden_id is not None else ()
    starts = await lease_starts(reads, [cell])
    inputs = CellPageInputs(
        cell=cell,
        warden=warden,
        tasks=[task for task in tasks if task.cell_id == cell_id],
        samples=telemetry_of(reads),
        grants=[grant_view(grant) for grant in grants],
        forage_events=await _forage_events(reads, [grant.id for grant in grants]),
        wax=await wax_notes(reads, cell_id, frozenset({WaxState.PROPOSED, WaxState.WRITTEN})),
        alarms=await alarm_views(reads, tasks),
        capping=await read_capping(reads),
        hosting=hosting_for(reads, [cell]),
        exoskeleton_edge=await _exoskeleton_edge(reads, cell_id),
        lease_since=starts.get(cell_id),
        as_of=now,
        last_patrol_at=await _last_patrol_at(reads, cell.warden_id),
    )
    return cell_page(inputs)


async def _forage_events(reads: HiveReads, grant_ids: list[str]) -> list[PheromoneEvent]:
    """Read each grant's newest forage.* events: its last request and the answer to it."""
    events: list[PheromoneEvent] = []
    for grant_id in grant_ids:
        query = TrailQuery(
            family="forage", subject_id=grant_id, newest_first=True, limit=FORAGE_EVENTS_PER_GRANT
        )
        # Latency: one local indexed trail read per grant.
        events.extend(await reads.trail.query(query))
    return events


async def _last_patrol_at(reads: HiveReads, warden_id: str | None) -> datetime | None:
    """Return when the Cell's Warden last ran a Patrol: its newest warden.patrol event, if any."""
    if warden_id is None:
        return None
    query = TrailQuery(kind=_PATROL_KIND, subject_id=warden_id, newest_first=True, limit=1)
    # Latency: one local indexed trail read.
    events = await reads.trail.query(query)
    return events[0].at if events else None


async def _exoskeleton_edge(reads: HiveReads, cell_id: CellId) -> str | None:
    """Return the kind of the Cell's newest attach or detach event; None if it never had one."""
    newest: PheromoneEvent | None = None
    for kind in _EXOSKELETON_KINDS:
        query = TrailQuery(kind=kind, subject_id=cell_id, newest_first=True, limit=1)
        # Latency: one local indexed trail read per edge kind.
        for event in await reads.trail.query(query):
            if newest is None or event.at > newest.at:
                newest = event
    return newest.kind if newest is not None else None
