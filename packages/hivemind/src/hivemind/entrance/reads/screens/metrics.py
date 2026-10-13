"""Read the metrics snapshot: every figure the Prometheus endpoint exposes.

The snapshot joins the summary strip's counters with Cells by status, Wardens by location, the
Forage ledger's grants and headroom, open Alarms, episodes per bee, tasks by status, tool
promotions and Guard denials counted on the trail, and tokens and cost per goal, slot and provider.
This module reads each once and hands them to the pure ``metrics_snapshot``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.metrics``. Calls into the census, the shared gathers, the
    cost read and the pure builder.

Key invariants:
    - Only reads.
    - Trail counts cover the newest ``MAX_QUERY_LIMIT`` events of their kind.

See Also:
    - hivemind.observation.metrics.snapshot for the snapshot.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.entrance.gate.reads import HiveReads
from hivemind.entrance.reads.census import cell_views, warden_views
from hivemind.entrance.reads.screens.cost import call_events, grant_goals
from hivemind.entrance.reads.screens.gather import (
    alarm_views,
    recent_episodes,
    telemetry_of,
    unfinished_tasks,
)
from hivemind.observation import (
    MetricsInputs,
    MetricsSnapshot,
    SummaryInputs,
    cost_lines,
    grant_view,
    headroom_view,
    metrics_snapshot,
    reserve_view,
    summary_view,
)
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery

TASKS_PER_STATUS = 10_000  # The chamber's page ceiling: a status with more is counted to it.
EPISODES_READ = 2_000  # Newest episode records read: an hour of a busy Hive's episodes.

__all__ = ["EPISODES_READ", "TASKS_PER_STATUS", "read_metrics"]


async def read_metrics(reads: HiveReads, now: datetime) -> MetricsSnapshot:
    """Read every figure the metrics endpoint exposes.

    Args:
        reads: The stores and live tables the Entrance reads.
        now: The instant the snapshot is for.

    Returns:
        The snapshot.
    """
    cells, wardens = await cell_views(reads), warden_views(reads)
    samples = telemetry_of(reads)
    episodes = await recent_episodes(reads, EPISODES_READ)
    alarms = await alarm_views(reads, await unfinished_tasks(reads))
    grants = [grant_view(grant) for grant in reads.ledger.live_grants()]
    inputs = MetricsInputs(
        summary=summary_view(SummaryInputs(cells, wardens, samples, episodes, alarms, now)),
        cells=cells,
        wardens=wardens,
        alarms=alarms,
        grants=grants,
        headroom=headroom_view(reads.ledger.headroom()),
        reserve=reserve_view(reads.ledger.reserve),
        episodes=episodes,
        task_counts=await _task_counts(reads),
        tool_promotions=await _count(reads, "tool.promoted"),
        denials=await _count(reads, "guard.denied"),
        cost_lines=cost_lines(await call_events(reads), await grant_goals(reads)),
        as_of=now,
    )
    return metrics_snapshot(inputs)


async def _task_counts(reads: HiveReads) -> dict[TaskStatus, int]:
    """Count tasks in each status."""
    counts: dict[TaskStatus, int] = {}
    for status in TaskStatus:
        # Latency: one local indexed chamber read per status, at most TASKS_PER_STATUS rows.
        counts[status] = len(
            await reads.chamber.list(TaskFilter(status=status, limit=TASKS_PER_STATUS))
        )
    return counts


async def _count(reads: HiveReads, kind: str) -> int:
    """Count the trail's events of one kind, newest ``MAX_QUERY_LIMIT`` at most."""
    # Latency: one local indexed trail read.
    return len(await reads.trail.query(TrailQuery(kind=kind, limit=MAX_QUERY_LIMIT)))
