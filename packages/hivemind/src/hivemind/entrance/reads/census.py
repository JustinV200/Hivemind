"""Build the Cells and Wardens views: each Cell and Warden, from every place that knows about it.

No single store holds a Cell's whole standing, so the census joins them, reading each directly
(ADR-0032): the Queen's attached Warden links (each carries its Cell record: kind, source, tier,
access level, capabilities), the Virtual Cell lifecycle's table (a Virtual Cell's status, and
Virtual Cells with no Warden attached yet or any more), the Pheromone Trail (a Cell's newest
lease edge and lifecycle event, its Warden's newest mode edge), the Brood Chamber (unfinished
tasks placed on each Cell), the Queen's pulse per Warden, the telemetry board (each Warden's
newest Heartbeat: its state and how many sub-bees it listed) and the Forage ledger (the grants each
Warden holds). The same views serve the read routes and the cell-status stream, which rebuilds
them whenever the trail moves.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads``. Called by
    ``hivemind.entrance.routes.hive.cells``, ``.wardens`` and the cell-status view. Calls into the
    Hive's stores through ``HiveReads`` and the view models.

Key invariants:
    - Only reads: nothing here changes a store, a table or the Queen.
    - A Cell is listed once, by its attached Warden's link when it has one.

See Also:
    - hivemind.entrance.models.views.cells and .wardens for the views built here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import AccessLevel, Cell, CellKind, CombShieldLevel, LeaseState, OsFamily
from hivemind.entrance.errors import CellNotFoundError
from hivemind.entrance.gate.reads import HiveReads
from hivemind.entrance.models.views import CellMode, CellView, WardenView
from hivemind.hive import LiveVirtualCell
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen import ForageLedger, WardenLink, WardenLiveness
from waggle.ids import CellId, TaskId, WardenId
from waggle.messages.supervision import WardenState

if TYPE_CHECKING:
    # Type-only: the streams package imports this one, so a runtime import here cycles.
    from hivemind.entrance.streams.telemetry import TelemetrySample

# The statuses in which a task holds a placement on a Cell (the Brood Chamber's own set).
PLACED = (TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PAUSED)
PLACED_PER_STATUS = 1_000  # Placed tasks read per status: a Hive runs far fewer at once.
EVENT_LOOKBACK = 25  # Newest events read per Cell or Warden to find its latest edge.
_LEASE_EDGES = {"cell.leased": LeaseState.OPEN, "cell.released": LeaseState.RELEASED}
# A Warden's mode edges on the trail; only ACTIVE and WATCH are modes a Cell page shows.
_MODE_EDGES: Mapping[str, CellMode | None] = {
    "warden.active": CellMode.ACTIVE,
    "warden.watch": CellMode.WATCH,
    "warden.clustered": None,
    "warden.offline": None,
    "warden.migrated": None,
    "warden.stopped": None,
}

__all__ = ["EVENT_LOOKBACK", "PLACED", "cell_view", "cell_views", "warden_views"]


async def cell_views(reads: HiveReads) -> tuple[CellView, ...]:
    """Build every Cell's view: attached Wardens' Cells first, then Virtual Cells without one.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        One view per Cell, each listed once.
    """
    placed = await _placed_tasks(reads)
    virtual = {live.cell_id: live for live in _live_virtual(reads)}
    found: list[_Found] = []
    # Attached first: a Warden's link carries the Cell record the Queen places work with.
    for link in reads.census.wardens:
        live = virtual.pop(link.cell.id, None)
        found.append(_Found(_record(link.cell), link.warden_id, live))
    # Then the lifecycle's Cells no Warden is attached to (provisioning, dormant, going away).
    for live in virtual.values():
        record = _record(live.cell) if live.cell is not None else _unrecorded(live)
        found.append(_Found(record, live.warden_id, live))
    return tuple([await _view(reads, one, placed) for one in found])


async def cell_view(reads: HiveReads, cell_id: CellId) -> CellView:
    """Build one Cell's view.

    Args:
        reads: The stores and live tables the Entrance reads.
        cell_id: The Cell.

    Returns:
        Its view.

    Raises:
        CellNotFoundError: No attached Warden supervises it and no lifecycle tracks it.
    """
    for view in await cell_views(reads):
        if view.id == cell_id:
            return view
    raise CellNotFoundError(cell_id)


def warden_views(reads: HiveReads) -> tuple[WardenView, ...]:
    """Build every attached Warden's view, in attachment order.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        One view per attached Warden.
    """
    latest, liveness = reads.telemetry.latest(), reads.census.liveness
    return tuple(
        _warden_view(link, liveness.get(link.warden_id), latest.get(link.warden_id), reads.ledger)
        for link in reads.census.wardens
    )


@dataclass(frozen=True, slots=True)
class _Record:
    """The part of a Cell's view its own record gives: identity, tiers, capabilities."""

    id: CellId
    name: str
    kind: CellKind
    source: str
    comb_shield: CombShieldLevel
    access_level: AccessLevel
    os: OsFamily | None
    has_display: bool
    has_browser: bool
    can_host_model: bool


@dataclass(frozen=True, slots=True)
class _Found:
    """What the census found for one Cell: its record, its Warden, its lifecycle row."""

    record: _Record
    warden_id: WardenId | None
    live: LiveVirtualCell | None


async def _view(reads: HiveReads, found: _Found, placed: Mapping[CellId, list[TaskId]]) -> CellView:
    """Join one Cell's record, lifecycle row, trail edges and placed tasks into its view."""
    record, live, warden_id = found.record, found.live, found.warden_id
    events = await _newest(reads, "cell", record.id)
    mode = await _mode(reads, warden_id) if warden_id is not None else None
    return CellView(
        id=record.id,
        name=record.name,
        kind=record.kind,
        source=record.source,
        comb_shield=record.comb_shield,
        access_level=record.access_level,
        mode=mode,
        # The newest lease edge decides: OPEN after cell.leased, RELEASED after cell.released.
        lease_state=next((_LEASE_EDGES[e.kind] for e in events if e.kind in _LEASE_EDGES), None),
        virtual_status=live.status if live is not None else None,
        last_event=events[0].kind if events else None,
        last_event_at=events[0].at if events else None,
        warden_id=warden_id,
        current_tasks=list(placed.get(record.id, [])),
        os=record.os,
        has_display=record.has_display,
        has_browser=record.has_browser,
        can_host_model=record.can_host_model,
    )


async def _mode(reads: HiveReads, warden_id: WardenId) -> CellMode | None:
    """Return the mode a Warden's newest mode edge on the trail left it in."""
    for event in await _newest(reads, "warden", warden_id):
        if event.kind in _MODE_EDGES:
            return _MODE_EDGES[event.kind]
    return None


async def _newest(reads: HiveReads, family: str, subject_id: str) -> tuple[PheromoneEvent, ...]:
    """Return the newest events of one family about one subject, newest first."""
    query = TrailQuery(
        family=family, subject_id=subject_id, newest_first=True, limit=EVENT_LOOKBACK
    )
    # Latency: one local indexed trail read.
    return await reads.trail.query(query)


async def _placed_tasks(reads: HiveReads) -> dict[CellId, list[TaskId]]:
    """Group every unfinished, placed task by the Cell it is placed on, oldest first."""
    placed: dict[CellId, list[TaskId]] = defaultdict(list)
    for status in PLACED:
        # Latency: one local indexed chamber read per placed status.
        for task in await reads.chamber.list(TaskFilter(status=status, limit=PLACED_PER_STATUS)):
            if task.cell_id is not None:
                placed[task.cell_id].append(task.id)
    return placed


def _live_virtual(reads: HiveReads) -> tuple[LiveVirtualCell, ...]:
    """Return every Virtual Cell the lifecycle tracks; none for a Hive with no Virtual side."""
    return reads.virtual_cells.live_cells() if reads.virtual_cells is not None else ()


def _record(cell: Cell) -> _Record:
    """Take a Cell record's identity, tiers and capability flags."""
    capabilities = cell.capabilities
    return _Record(
        id=cell.id,
        name=cell.name,
        kind=cell.kind,
        source=cell.source,
        comb_shield=cell.comb_shield,
        access_level=cell.access_level,
        os=capabilities.os,
        has_display=capabilities.has_display,
        has_browser=capabilities.has_browser,
        can_host_model=capabilities.can_host_model,
    )


def _unrecorded(live: LiveVirtualCell) -> _Record:
    """Describe a Virtual Cell known only by its lifecycle row (reconciled after a restart).

    Such a row carries no Cell record, only its image, backend and tier; a Virtual Cell is always
    FULL access, and nothing is claimed about capabilities nobody reported.
    """
    return _Record(
        id=live.cell_id,
        name=live.image,
        kind=CellKind.VIRTUAL,
        source=live.backend,
        comb_shield=live.comb_shield,
        access_level=AccessLevel.FULL,
        os=None,
        has_display=False,
        has_browser=False,
        can_host_model=False,
    )


def _warden_view(
    link: WardenLink,
    pulse: WardenLiveness | None,
    sample: TelemetrySample | None,
    ledger: ForageLedger,
) -> WardenView:
    """Join one Warden's link, pulse, newest Heartbeat and grants into its view."""
    offline = pulse is not None and pulse.is_offline
    heartbeat = sample.heartbeat if sample is not None else None
    state = heartbeat.warden_state if heartbeat is not None else None
    return WardenView(
        id=link.warden_id,
        cell_id=link.cell.id,
        # The Queen's own judgement wins: a Warden she marked offline is OFFLINE, whatever it said.
        state=WardenState.OFFLINE if offline else state,
        offline=offline,
        last_heartbeat_at=pulse.last_heartbeat_at if pulse is not None else None,
        missed_heartbeats=pulse.missed_heartbeats if pulse is not None else 0,
        sub_bees=len(heartbeat.children) if heartbeat is not None else 0,
        live_grants=len(ledger.grants_for(link.warden_id)),
    )
