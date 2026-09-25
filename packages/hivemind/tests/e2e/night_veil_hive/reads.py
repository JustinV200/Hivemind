"""Read what a Night Veil run left on the Queen's trail and in her Brood Chamber, by id.

"About the Night Veil work" is read by id: an event is about it when its subject or payload names
one of its Cells, its tasks, their grants or the Cells' Wardens (`world`), or when a node other
than the Queen's recorded it. `leaks` names every such event on the durable trail that is more
than its skeleton (`skeletal`: a skeleton kind cut to its skeleton payload, or the purge's own
counts). The goal request's own `queen.goal_request_*` records are the human's request rather
than the Cell's work: they name the goal they planned by id and carry no words
(`hivemind.pheromone.events.families.supervisors`), so they are the one family besides the
skeleton allowed to name the task. The rest are the small waits and reads every scenario shares.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests.e2e.test_night_veil_boundary.

Key invariants:
    - Nothing here writes: every function reads a store or a trail the Hive already holds.

See Also:
    - hivemind.pheromone.retention.skeleton for what may outlive a Night Veil Cell.
    - tests.e2e.night_veil_hive.stores for what the Hive's other stores hold.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from e2e.night_veil_hive.rig import EVERYTHING, NightVeilRun

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.pheromone import (
    MAX_QUERY_LIMIT,
    SKELETON_KINDS,
    PheromoneEvent,
    TrailQuery,
    skeleton_event,
)

__all__ = [
    "PURGED",
    "counted",
    "durable",
    "kinds_about",
    "leaks",
    "night_veil_cells",
    "succeeded",
    "tasks",
    "working",
    "world",
]

PURGED = "cell.purged"  # The purge's own record: counts only.
_PURGE_COUNTS = {"events_purged", "side_channel_records_purged"}
_REQUEST_RECORDS = "queen.goal_request_"  # The human request's own family (module docstring).


async def tasks(run: NightVeilRun) -> tuple[Task, ...]:
    """Every task in the Hive's Brood Chamber: the one the goal was planned into."""
    return tuple(await run.hive.stores.chamber.list(TaskFilter()))


async def succeeded(run: NightVeilRun) -> bool:
    """Whether every task of the goal succeeded; fails at once, naming why, if one ended otherwise.

    Failing fast turns a task that ended early (a grant a loaded host denies, say) into its own
    outcome in the report, rather than a timeout further on that names nothing.
    """
    found = await tasks(run)
    for task in found:
        assert task.status is TaskStatus.SUCCEEDED or not is_terminal(task.status), task.outcome
    return bool(found) and all(task.status is TaskStatus.SUCCEEDED for task in found)


async def durable(run: NightVeilRun) -> tuple[PheromoneEvent, ...]:
    """Every event on the Queen's durable trail (the boundary's reads pass straight through)."""
    return await run.hive.stores.trail.query(EVERYTHING)


async def counted(run: NightVeilRun, kind: str, expected: int) -> bool:
    """Whether the durable trail holds exactly `expected` events of `kind`."""
    query = TrailQuery(kind=kind, limit=MAX_QUERY_LIMIT)
    return len(await run.hive.stores.trail.query(query)) == expected


async def working(run: NightVeilRun) -> bool:
    """Whether a Cell's Worker holds, all it shipped merged; fails fast if it never can hold.

    The hold ships the Cell's trail before it waits, but the Queen merges it on her own side of
    the link: only once every id a hold shipped is in a held segment is the Cell's record there.
    """
    for task in await tasks(run):
        assert not is_terminal(task.status), task.outcome  # Ended before its Worker could hold.
    segments = run.night_veil.segments
    merged = {
        e.id for cell in segments.held_cells() for e in await segments.query(cell, EVERYTHING)
    }
    return bool(run.held) and all(shipped <= merged for shipped in run.held)


def night_veil_cells(events: Iterable[PheromoneEvent]) -> list[str]:
    """Every Night Veil Cell the skeleton's `cell.provisioned` names, in provisioning order."""
    return [
        e.subject_id
        for e in events
        if e.kind == "cell.provisioned" and e.payload.get("comb_shield") == "NIGHT_VEIL"
    ]


def world(
    events: Sequence[PheromoneEvent], found: Iterable[Task], run: NightVeilRun
) -> frozenset[str]:
    """Every id the Night Veil work goes by: its Cells, tasks, grants and in-Cell Wardens."""
    task_ids = {task.id for task in found}
    grants = {
        e.subject_id
        for e in events
        if e.kind == "forage.granted" and e.payload.get("task_id") in task_ids
    }
    wardens = {deps.hop.sender for deps in run.in_cell}
    return frozenset({*night_veil_cells(events), *task_ids, *grants, *wardens})


def leaks(events: Sequence[PheromoneEvent], found: Iterable[Task], run: NightVeilRun) -> list[str]:
    """Every durable event about the Night Veil work that is more than its skeleton."""
    ids = world(events, found, run)
    queen_node = str(run.hive.manifest.hive.node_id)
    about = [
        e
        for e in events
        if e.node_id != queen_node
        or e.subject_id in ids
        or any(member in json.dumps(e.payload) for member in ids)
    ]
    return [f"{e.kind} {e.subject_id}" for e in about if not _skeletal(e, queen_node)]


def kinds_about(events: Iterable[PheromoneEvent], subject: str) -> list[str]:
    """The kinds of every event about `subject`, in trail order."""
    return [e.kind for e in events if e.subject_id == subject]


def _skeletal(event: PheromoneEvent, queen_node: str) -> bool:
    """Whether `event` may outlive a Night Veil Cell: a skeleton row, cut, from the Queen."""
    if event.node_id != queen_node:
        return False  # A row the Cell's own node recorded: its local detail.
    if event.kind == PURGED:
        return set(event.payload) == _PURGE_COUNTS
    if event.kind.startswith(_REQUEST_RECORDS):
        return True  # The human's request's own records (module docstring).
    return event.kind in SKELETON_KINDS and skeleton_event(event) == event
