"""Checkpoint and pause every bee on an isolated Cell, waiting a bounded time for each to answer.

An isolation (roadmap step 10.6a, ADR-0043) stops the Cell's work without destroying it: every
task placed on the Cell is asked to checkpoint and pause with the levers the Queen already pulls
for Clustering (codingrules 8.9, "one mechanism, many names"): `Intervene(HANDOFF)` so the bee
writes its Handoff, then `TaskPause`, both relayed by the Cell's own Warden. A bee answers by
recording `worker.paused` (or stopping outright: `worker.done`, `.killed`, `.failed`) on its
Cell's trail segment, which reaches the Queen's trail through the Warden's trail shipping (the
Hive Stand's bees write to her trail directly), or, for a Night Veil Cell, only the Cell's own
ephemeral segment on her side, which the wait reads too (`query_cell`). The wait is bounded by
`[guard]`'s pause timeout on the injected clock, never a real timer, and never an error: a bee
that does not answer is named in `cell.isolated` as unacknowledged, and the isolation goes on,
because the grant is already revoked and the Cell is about to lose its egress. Last, each task
moves to PAUSED in the Brood Chamber (a task BLOCKED on a Question has the Question withdrawn
first, as a quarantine's hold does), so its way back is `hivemind.queen.resume_paused` once the
human lifts the isolation.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by `hivemind.queen.isolation.path`. Calls into
    `hivemind.brood_chamber`, `hivemind.pheromone` (TrailQuery, query_cell),
    `hivemind.supervision` (Handoff, to_intervene) and waggle only; `QueenDeps` only for its
    type.

Key invariants:
    - The wait never outlasts `pause_timeout_s` on `deps.clock`; a zero timeout checks once.
    - Only a task RUNNING when the wait ends moves to PAUSED; an ASSIGNED task whose bee never
      started stays ASSIGNED (ASSIGNED -> PAUSED is not an edge), its grant already revoked.

See Also:
    - hivemind.queen.cluster.protocol for the same lever pair under Clustering.
    - hivemind.wardens.ticks.heartbeat for the Warden shipping its trail on a bee's pause.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task, TaskFilter, TaskNotFoundError, TaskStatus
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery, query_cell
from hivemind.supervision import Handoff, to_intervene
from waggle.envelope import wrap
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.ids import CellId, TaskId
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.task import TaskPause

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink

# A bee's answer to the pause: it paused, or it stopped for good on its own.
ACK_KINDS = ("worker.paused", "worker.done", "worker.killed", "worker.failed")
SPAWNED_KIND = "worker.spawned"  # The Warden's own record: subject is the bee, payload its task.
POLL_INTERVAL_S = 0.05  # Short enough to see a prompt answer, coarse enough not to spin.
# Where a task on the Cell can be: the placed statuses a bee may be working in.
_PLACED = (TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED)

__all__ = ["ACK_KINDS", "POLL_INTERVAL_S", "SPAWNED_KIND", "PauseOutcome", "pause_cell_bees"]


@dataclass(frozen=True, slots=True)
class PauseOutcome:
    """What pausing a Cell's bees did.

    Attributes:
        paused: Tasks moved to PAUSED in the Brood Chamber.
        unacknowledged: Tasks asked to pause whose bee did not answer within the bound.
    """

    paused: tuple[TaskId, ...]
    unacknowledged: tuple[TaskId, ...]


async def pause_cell_bees(
    deps: QueenDeps, link: WardenLink, reason: str, timeout_s: float
) -> PauseOutcome:
    """Checkpoint and pause every bee on `link`'s Cell; wait up to `timeout_s` for answers.

    Args:
        deps: The Queen's collaborators.
        link: The isolated Cell's Warden, which relays both levers to its bees.
        reason: Why, naming ids only; it travels on both levers and the chamber's rows.
        timeout_s: How long to wait for the bees' answers, on `deps.clock`.

    Returns:
        The tasks paused, and the ones whose bee did not answer in time.
    """
    since = deps.clock.now()
    tasks = await _placed_on(deps, link.cell.id)
    for task in tasks:
        await _send_levers(deps, link, task.id, reason)
    asked = frozenset(task.id for task in tasks)
    answered = await _await_answers(deps, link.cell.id, asked, since, timeout_s)
    paused = [task.id for task in tasks if await _hold(deps, task.id, reason)]
    unanswered = tuple(task.id for task in tasks if task.id not in answered)
    return PauseOutcome(paused=tuple(paused), unacknowledged=unanswered)


async def _placed_on(deps: QueenDeps, cell_id: CellId) -> tuple[Task, ...]:
    """Return every task placed on `cell_id` in a status its bee may be working in."""
    found: list[Task] = []
    # The chamber filters by status, not by Cell; a task's placement names its Cell.
    for status in _PLACED:
        listed = await deps.chamber.list(TaskFilter(status=status, limit=MAX_TASK_FILTER_LIMIT))
        found.extend(task for task in listed if task.cell_id == cell_id)
    return tuple(found)


async def _send_levers(deps: QueenDeps, link: WardenLink, task_id: TaskId, reason: str) -> None:
    """Send `Intervene(HANDOFF)` then `TaskPause` for `task_id`, Clustering's own pair."""
    bounded = reason[:MAX_REASON_CHARS]
    try:
        # HANDOFF first so the bee writes its checkpoint; TaskPause then holds it where it is.
        lever = to_intervene(Handoff(reason=bounded), task_id=task_id)
        await link.transport.send(wrap(lever, link.hop, clock=deps.clock))
        pause = TaskPause(task_id=task_id, reason=bounded)
        await link.transport.send(wrap(pause, link.hop, clock=deps.clock))
    except (TransportClosedError, ConnectionLostError):
        return  # A lost Warden relays nothing; its bee is unacknowledged, the rest goes on.


async def _await_answers(
    deps: QueenDeps, cell_id: CellId, asked: frozenset[TaskId], since: datetime, timeout_s: float
) -> frozenset[TaskId]:
    """Poll the trail until every asked task's bee answered, or `timeout_s` passes."""
    deadline = deps.clock.monotonic() + timeout_s
    while True:
        answered = await _answered(deps, cell_id, since) & asked
        remaining = deadline - deps.clock.monotonic()
        if answered == asked or remaining <= 0:
            return answered
        # The injected clock, as every bounded wait in the Queen (cell_gate.quiesce): a test's
        # FakeClock drives it, and the Warden's trail shipping runs while this sleeps.
        await deps.clock.sleep(min(POLL_INTERVAL_S, remaining))


async def _answered(deps: QueenDeps, cell_id: CellId, since: datetime) -> frozenset[TaskId]:
    """Return every task whose bee recorded an answer at or after `since`, on `cell_id` or not."""
    bees: set[str] = set()
    for kind in ACK_KINDS:
        # A Night Veil Cell's bees answer into its segment alone: `query_cell` reads it too.
        query = TrailQuery(kind=kind, since=since, limit=MAX_QUERY_LIMIT)
        bees.update(event.subject_id for event in await query_cell(deps.trail, cell_id, query))
    tasks: set[TaskId] = set()
    # A bee's answer names only the bee; its Warden's spawn record names the task it runs.
    for bee in bees:
        query = TrailQuery(kind=SPAWNED_KIND, subject_id=bee, newest_first=True, limit=1)
        spawned = await query_cell(deps.trail, cell_id, query)
        if spawned and spawned[0].payload.get("task_id"):
            tasks.add(TaskId(str(spawned[0].payload["task_id"])))
    return frozenset(tasks)


async def _hold(deps: QueenDeps, task_id: TaskId, reason: str) -> bool:
    """Move `task_id` to PAUSED if it is running (withdrawing a pending Question first)."""
    try:
        task = await deps.chamber.get(task_id)
    except TaskNotFoundError:
        return False  # Gone from the chamber since the list: nothing to pause.
    if task.status is TaskStatus.BLOCKED and task.pending_question_id is not None:
        # BLOCKED -> PAUSED is not an edge; the Question is withdrawn first, as a quarantine's
        # hold does, and the human sees the task paused rather than waiting on an answer.
        task = await deps.chamber.withdraw(task.pending_question_id, reason)
    if task.status is not TaskStatus.RUNNING:
        return False  # ASSIGNED (never started), finished meanwhile, or paused already.
    await deps.chamber.pause(task_id, reason)
    return True
