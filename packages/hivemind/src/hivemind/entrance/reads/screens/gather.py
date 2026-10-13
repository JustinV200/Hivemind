"""Gather the records several Observation Hive screens share: tasks, Alarms, telemetry and more.

Each screen builder in ``hivemind.observation`` is pure, so the Entrance does the reading
(ADR-0040: reads go to the stores directly). The same few reads feed most screens: the unfinished
tasks, the tasks that finished lately, the open Alarms folded from the trail, the newest telemetry
sample of every bee, recent episode records, Cell Wax, the hosting plans and when each open lease
began. They live here once, each a thin read that hands plain records to a builder, so a screen's
module says only which of them it needs.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by the sibling screen modules. Calls into the Hive's stores through ``HiveReads`` and the
    observation face's pure folds.

Key invariants:
    - Only reads: nothing here changes a store, a table or the Queen.
    - Every read is bounded: a status page, a trail window or an episode count.

See Also:
    - hivemind.entrance.reads.census for the Cell and Warden views these screens join.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from hivemind.brood_chamber import Task, TaskFilter, TaskNotFoundError, TaskStatus
from hivemind.cell import HIVE_STAND_SOURCE, HoneyClearance, LeaseState
from hivemind.entrance.gate.reads import HiveReads
from hivemind.memory import CellWax, EpisodeRecord, WaxState
from hivemind.observation import (
    ALARM_FAMILY,
    CellView,
    HostingFacts,
    OpenAlarmView,
    TelemetrySampleView,
    hosting_facts,
    open_alarms,
    telemetry_samples,
)
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.ids import CellId, TaskId

UNFINISHED = (
    TaskStatus.PENDING,
    TaskStatus.ASSIGNED,
    TaskStatus.RUNNING,
    TaskStatus.BLOCKED,
    TaskStatus.PAUSED,
)  # The statuses of work still in play.
PER_STATUS = 1_000  # Unfinished tasks read per status: a Hive runs far fewer at once.
FINISHED_KINDS = ("task.succeeded", "task.failed", "task.cancelled")  # The trail's ends of a task.
FINISHED_READ = 30  # Newest finishing events read per kind to find recently finished tasks.

__all__ = [
    "FINISHED_KINDS",
    "FINISHED_READ",
    "PER_STATUS",
    "UNFINISHED",
    "alarm_views",
    "hosting_for",
    "lease_starts",
    "recent_episodes",
    "recent_finished",
    "telemetry_of",
    "unfinished_tasks",
    "wax_notes",
]


async def unfinished_tasks(reads: HiveReads) -> list[Task]:
    """Read every task still in play, oldest first within each status.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        Up to ``PER_STATUS`` tasks of each unfinished status.
    """
    tasks: list[Task] = []
    for status in UNFINISHED:
        # Latency: one local indexed chamber read per unfinished status.
        tasks.extend(await reads.chamber.list(TaskFilter(status=status, limit=PER_STATUS)))
    return tasks


async def recent_finished(reads: HiveReads) -> list[Task]:
    """Read the tasks that finished most recently, found through the trail's finishing events.

    The Brood Chamber lists oldest first with no newest-first order, so the newest finishes are
    found where they were recorded: ``task.succeeded``, ``failed`` and ``cancelled`` events.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        The newest finished tasks, newest first (at most ``FINISHED_READ`` per kind).
    """
    events: list[PheromoneEvent] = []
    for kind in FINISHED_KINDS:
        # Latency: one local indexed trail read per finishing kind.
        events.extend(
            await reads.trail.query(TrailQuery(kind=kind, newest_first=True, limit=FINISHED_READ))
        )
    events.sort(key=lambda event: event.at, reverse=True)
    tasks: list[Task] = []
    # Each task once, in the order of its newest finishing event.
    for subject in dict.fromkeys(event.subject_id for event in events):
        try:
            tasks.append(await reads.chamber.get(TaskId(subject)))
        except TaskNotFoundError:
            continue  # A stray event for a task this chamber does not hold.
    return tasks


async def alarm_views(reads: HiveReads, tasks: Iterable[Task] = ()) -> tuple[OpenAlarmView, ...]:
    """Fold the trail's Alarm events into the open Alarms, placed on Cells where possible.

    Args:
        reads: The stores and live tables the Entrance reads.
        tasks: Tasks that place an Alarm on a Cell through its task.

    Returns:
        The open Alarms, oldest first.
    """
    # Latency: one local indexed trail read, at most MAX_QUERY_LIMIT events.
    events = await reads.trail.query(
        TrailQuery(family=ALARM_FAMILY, newest_first=True, limit=MAX_QUERY_LIMIT)
    )
    by_task: dict[str, str] = {task.id: task.cell_id for task in tasks if task.cell_id}
    by_warden: dict[str, str] = {link.warden_id: link.cell.id for link in reads.census.wardens}
    return open_alarms(events, by_task, by_warden)


def telemetry_of(reads: HiveReads) -> list[TelemetrySampleView]:
    """Split every Warden's newest Heartbeat into a sample per bee.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        The Warden's own sample, then one per sub-bee, for each Warden that has reported.
    """
    samples: list[TelemetrySampleView] = []
    for latest in reads.telemetry.latest().values():
        samples.extend(telemetry_samples(latest.warden_id, latest.heartbeat, latest.at))
    return samples


async def recent_episodes(reads: HiveReads, count: int) -> tuple[EpisodeRecord, ...]:
    """Read the newest episode records of every bee.

    Args:
        reads: The stores and live tables the Entrance reads.
        count: The most records to read.

    Returns:
        The records, newest first. Counted by the callers, never shown: the screens that use them
        under ``observe`` carry numbers only, so reading every clearance is safe.
    """
    # Latency: one local indexed read of the memory tables.
    return await reads.memory.list_episodes(None, HoneyClearance.C2, count)


async def wax_notes(
    reads: HiveReads, cell_id: CellId | None, states: frozenset[WaxState]
) -> tuple[CellWax, ...]:
    """Read Cell Wax notes in some states, for one Cell or every Cell.

    Args:
        reads: The stores and live tables the Entrance reads.
        cell_id: One Cell's id, or None for every Cell's.
        states: The states to keep.

    Returns:
        The notes, newest first. The views built from them carry no note text.
    """
    # Latency: one local indexed read of the memory tables.
    return await reads.memory.list_wax(cell_id, states, HoneyClearance.C2)


def hosting_for(reads: HiveReads, cells: Sequence[CellView]) -> HostingFacts:
    """Index the hosting plans of these Cells and the Forage map's hosts.

    Args:
        reads: The stores and live tables the Entrance reads.
        cells: The Cells whose plans are wanted.

    Returns:
        The facts; with no Forage map wired every Cell's hosting classifies as unknown.
    """
    decisions = reads.ledger.decisions
    plans = [plan for cell in cells if (plan := decisions.plan_for(cell.id)) is not None]
    sources = reads.forage_map.sources() if reads.forage_map is not None else ()
    stands = [cell.id for cell in cells if cell.source == HIVE_STAND_SOURCE]
    return hosting_facts(plans, sources, stands)


async def lease_starts(reads: HiveReads, cells: Sequence[CellView]) -> dict[str, datetime]:
    """Find when each open Real Cell lease began, from the newest ``cell.leased`` edge.

    Args:
        reads: The stores and live tables the Entrance reads.
        cells: The Cells to look at.

    Returns:
        Each Cell with an OPEN lease and its lease's start.
    """
    starts: dict[str, datetime] = {}
    for cell in cells:
        if cell.lease_state is not LeaseState.OPEN:
            continue
        query = TrailQuery(kind="cell.leased", subject_id=cell.id, newest_first=True, limit=1)
        # Latency: one local indexed trail read per open lease.
        edges = await reads.trail.query(query)
        if edges:
            starts[cell.id] = edges[0].at
    return starts
