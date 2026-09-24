"""Let a quarantined task out once a judge clears its checkpoint: the Queen's half of the way out.

ADR-0035: "the only way out is a respawn from a Handoff the judge has cleared". The Warden's gate
(`hivemind.wardens.quarantine.gate`) admits exactly that respawn and nothing else; this module is
what sends it (roadmap step 10.6a, closing 10.6c's un-orchestrated way out). The trigger is the
trail, not a caller: a judge's verdict records `memory.taint_cleared` about the checkpoint the
quarantine wrote (`warden.intervened` names it), wherever the judge ran, and the Queen reads both
on her tick. So a clearing that happened while she was down still lets its task out after the
restart, and nothing here needs to know who ran the judge. `resume_cleared` resumes each PAUSED
task whose quarantine checkpoint was cleared after the task was last paused, from that checkpoint,
through `hivemind.queen.resume_paused` (a fresh grant, then the TaskAssign the gate admits). The
"after it was last paused" rule is what makes it safe to run on every tick: a resume the gate
refuses pauses the task again, later than the clearing, and it is not retried until a new one.
A task on a Cell that is isolated stays where it is: only the human's lift reopens that Cell.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    quarantine sub-package. Called by `hivemind.queen.queen`'s tick, once per tick. Calls into
    `hivemind.brood_chamber`, `hivemind.pheromone` (TrailQuery), `hivemind.queen.deps`,
    `hivemind.queen.dispatcher` (resume_paused), `hivemind.queen.isolation` (read_isolation) and
    waggle only.

Key invariants:
    - A task resumes only from the exact checkpoint its quarantine wrote, and only once a
      `memory.taint_cleared` about it is newer than the task's last `task.paused`.
    - Reads the trail only when some task is PAUSED; costs nothing otherwise.

See Also:
    - hivemind.wardens.quarantine.gate for the Warden's half of the way out.
    - hivemind.memory.taint.clear for the one clearer, and its `memory.taint_cleared` event.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import resume_paused
from hivemind.queen.isolation import IsolationState, read_isolation
from waggle.ids import EventId, TaskId, timestamp_of
from waggle.messages import HandoffRef

INTERVENED_KIND = "warden.intervened"  # A quarantine: payload names the task and its checkpoint.
CLEARED_KIND = "memory.taint_cleared"  # A judge's verdict: subject is the cleared item's id.
PAUSED_KIND = "task.paused"  # The chamber's pause: subject is the task.
RESUME_REASON = "A judge cleared the checkpoint its quarantine wrote."

__all__ = ["CLEARED_KIND", "INTERVENED_KIND", "PAUSED_KIND", "RESUME_REASON", "resume_cleared"]


async def resume_cleared(deps: QueenDeps, wardens: Sequence[WardenLink]) -> tuple[TaskId, ...]:
    """Resume every PAUSED task whose quarantine checkpoint a judge cleared since its pause.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden attached now; a task resumes through its own.

    Returns:
        The tasks resumed this call, in the chamber's order.
    """
    paused = await deps.chamber.list(
        TaskFilter(status=TaskStatus.PAUSED, limit=MAX_TASK_FILTER_LIMIT)
    )
    if not paused:
        return ()  # The common case: nothing is held, and the trail is never read.
    checkpoints = await _quarantine_checkpoints(deps)
    resumed: list[TaskId] = []
    for task in paused:
        checkpoint = checkpoints.get(task.id)
        # Paused by Clustering or an isolation, not a quarantine: not this way out.
        if checkpoint is None or not await _cleared_since_paused(deps, task.id, checkpoint):
            continue
        if await _on_isolated_cell(deps, task):
            continue  # The human's lift reopens an isolated Cell; nothing resumes there before.
        await resume_paused(deps, wardens, task.id, _ref(task, checkpoint), RESUME_REASON)
        resumed.append(task.id)
    return tuple(resumed)


async def _quarantine_checkpoints(deps: QueenDeps) -> Mapping[TaskId, EventId]:
    """Return each quarantined task's newest quarantine checkpoint, from `warden.intervened`."""
    query = TrailQuery(kind=INTERVENED_KIND, newest_first=True, limit=MAX_QUERY_LIMIT)
    checkpoints: dict[TaskId, EventId] = {}
    for event in await deps.trail.query(query):
        task_id, handoff = event.payload.get("task_id"), event.payload.get("handoff_event_id")
        # Newest first: a task quarantined twice resumes only from its latest checkpoint.
        if task_id and handoff and TaskId(str(task_id)) not in checkpoints:
            checkpoints[TaskId(str(task_id))] = EventId(str(handoff))
    return checkpoints


async def _cleared_since_paused(deps: QueenDeps, task_id: TaskId, checkpoint: EventId) -> bool:
    """Whether a judge cleared `checkpoint` after `task_id` was last paused."""
    cleared = await _newest(deps, CLEARED_KIND, checkpoint)
    if cleared is None:
        return False  # Still tainted: no verdict, or none that cleared it.
    paused = await _newest(deps, PAUSED_KIND, task_id)
    # Strictly later: a clearing recorded in the very instant of a pause (never a real judge's
    # timing) waits for the next one rather than risking a resume the gate just refused.
    return paused is None or cleared.at > paused.at


async def _on_isolated_cell(deps: QueenDeps, task: Task) -> bool:
    """Whether `task` is placed on a Cell that is isolated now."""
    if task.cell_id is None:
        return False
    return (await read_isolation(deps, task.cell_id)).state is IsolationState.ISOLATED


async def _newest(deps: QueenDeps, kind: str, subject_id: str) -> PheromoneEvent | None:
    """Return the newest `kind` event about `subject_id`, or None."""
    query = TrailQuery(kind=kind, subject_id=subject_id, newest_first=True, limit=1)
    events = await deps.trail.query(query)
    return events[0] if events else None


def _ref(task: Task, checkpoint: EventId) -> HandoffRef:
    """The reference the gate admits: the quarantine checkpoint itself, at the task's clearance."""
    return HandoffRef(
        event_id=checkpoint,
        written_at=timestamp_of(checkpoint),  # Its id's own instant: the ULID the write minted.
        clearance=task.spec.clearance.to_wire(),
    )
