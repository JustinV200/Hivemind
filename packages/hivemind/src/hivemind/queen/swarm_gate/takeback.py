"""Define take_back: take a device's tasks from its Warden so they can be placed afresh.

Four things end a device Warden's hold on its tasks for good, and none of them may leave a task
RUNNING on a Warden that is no longer there: the Queen gave up on a device that stayed away
(ADR-0053), the operator revoked it, the operator changed its terms so its Warden is rebuilt, or
the Warden's own loop died. In each the answer is the same, and it is the same mechanism a
Warden migration uses (codingrules 8.9, "one mechanism, many names"): a task that is running is
paused (RUNNING has no edge back to PENDING), its newest Handoff is noted so its next placement
resumes from it (`DispatchBook.resumes`; the dispatcher carries the document inline when the new
Warden is on another node), and it is unassigned, which puts it back to PENDING with a new attempt
for the ordinary dispatcher to place on whatever Cell fits. The newest Handoff is the newest the
Queen can hold (`recall`): a colonized device's own store is lost with it, so what she has of it is
the HANDOFF Nectar its bees deposited, recalled into her memory if it may be used. A task blocked on
a Question has the Question withdrawn first, since a Warden that is gone will never be given the
Answer. The Warden's grants go back to the pool: it draws on them no more.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Called by `hivemind.queen.swarm_gate.unreachable` (a decision to re-place) and by
    `hivemind.queen.swarm_gate.reconcile` (a revoke, a rebuild, a failed Warden); `open_tasks`,
    which lists what has not ended, is also read by the colonize flows and by
    `hivemind.queen.swarm_gate.rungs` (whose pass reads the Hive once). Calls into
    `hivemind.brood_chamber`, `hivemind.forage`, `hivemind.queen.forage.grants`,
    `hivemind.queen.trail`, `hivemind.swarm` and this package's `recall` (the newest Handoff) only.

Key invariants:
    - A task that ended, or moved on its own while this ran, is left exactly as it is; one task
      that cannot be taken back never stops the others.
    - A task is never failed or cancelled here: it goes back to PENDING and keeps its goal.
    - The newest Handoff is noted only if the Queen holds one or `recall` can give her one; with
      none the task starts fresh.
    - The decision is on the trail: one `queen.decided` naming the device, the cause and the count.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for when a task is taken back.
    - hivemind.queen.swarm_gate.recall for how a Handoff only the Honey Store holds is found.
    - hivemind.queen.dispatcher.ready.handoff for how the noted Handoff travels to its new Warden.
    - hivemind.brood_chamber.chamber.lifecycle for `unassign`, including from PAUSED.
"""

from __future__ import annotations

from collections.abc import Collection

from hivemind.brood_chamber import InvalidTransitionError, Task, TaskFilter, TaskStatus
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.common.logging import get_logger
from hivemind.forage.grant_state import GrantState
from hivemind.queen.deps import QueenDeps
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.swarm_gate.recall import recall_handoff
from hivemind.queen.trail import record_event
from hivemind.swarm import SwarmNode
from waggle.ids import TaskId, WardenId
from waggle.messages.forage.values import RevocationCause

# The statuses of a task that has not ended. The chamber filters by one status at a time, so the
# unfinished tasks are read status by status rather than out of the oldest page of all of them.
_UNFINISHED = (
    TaskStatus.PENDING,
    TaskStatus.ASSIGNED,
    TaskStatus.RUNNING,
    TaskStatus.BLOCKED,
    TaskStatus.PAUSED,
)

log = get_logger(__name__)

__all__ = ["open_tasks", "take_back", "tasks_on"]


async def open_tasks(deps: QueenDeps) -> tuple[Task, ...]:
    """Return every task of the Brood Chamber that has not ended, oldest first.

    Read status by status and page by page, so a Hive that has finished thousands of tasks still
    finds the few that are unfinished: the default read is the oldest thousand of any status.

    Args:
        deps: The Queen's collaborators.

    Returns:
        The pending, assigned, running, blocked and paused tasks, in creation order. A pending one
        is placed nowhere yet (`cell_id` is None).
    """
    found: list[Task] = []
    for status in _UNFINISHED:
        query = TaskFilter(status=status, limit=MAX_TASK_FILTER_LIMIT)
        while page := await deps.chamber.list(query):
            found.extend(page)
            if len(page) < MAX_TASK_FILTER_LIMIT:
                break
            query = query.model_copy(update={"after": page[-1].id})
    return tuple(sorted(found, key=lambda task: (task.created_at, task.id)))


async def tasks_on(deps: QueenDeps, node: SwarmNode) -> list[Task]:
    """Return the unfinished tasks placed on `node`'s Cell, oldest first.

    Args:
        deps: The Queen's collaborators.
        node: The device whose Cell is looked at.

    Returns:
        Every task of the Brood Chamber that is placed there and has not ended.
    """
    return [task for task in await open_tasks(deps) if task.cell_id == node.cell_id]


async def take_back(
    deps: QueenDeps, node: SwarmNode, cause: str, only: Collection[TaskId] | None = None
) -> tuple[TaskId, ...]:
    """Take unfinished tasks off `node`'s Warden, and give its grants back to the pool.

    Args:
        deps: The Queen's collaborators.
        node: The device whose Warden's tasks go back to PENDING.
        cause: A short stable word for why (`grace_over`, `revoked`, `rebuilt`, `failed`), recorded.
        only: Take back just these tasks and leave the Warden's grants alone (a few tasks of a
            Warden that goes on, such as ones a migration could not resume); None takes them all.

    Returns:
        The ids of the tasks now PENDING again, oldest first.
    """
    tasks = [task for task in await tasks_on(deps, node) if only is None or task.id in only]
    taken = [task.id for task in tasks if await _take_one(deps, node, task, cause)]
    if only is None:
        await _release_grants(deps, node.warden_id)  # The Warden is gone: nothing draws on them.
    if taken:
        await record_event(
            deps,
            "queen.decided",
            node.device_id,
            reason="tasks_taken_back",
            detail=cause,
            task_ids=list(taken),
        )
    return tuple(taken)


async def _take_one(deps: QueenDeps, node: SwarmNode, task: Task, cause: str) -> bool:
    """Take one task back; False when it moved or ended by itself while this was deciding."""
    # Read before the task leaves its Warden: it is the newest Handoff of the bee that ran it.
    ref = None if task.status is TaskStatus.ASSIGNED else await recall_handoff(deps, node, task)
    reason = f"Its device is gone ({cause}); the Queen takes the task back to place it afresh."
    try:
        if task.pending_question_id is not None:
            await deps.chamber.withdraw(task.pending_question_id, reason)  # BLOCKED -> RUNNING.
        if task.status in (TaskStatus.RUNNING, TaskStatus.BLOCKED):
            await deps.chamber.pause(task.id, reason)  # RUNNING has no edge back to PENDING.
        await deps.chamber.unassign(task.id, reason)
    except InvalidTransitionError:
        log.info("swarm.take_back_skipped", task_id=task.id, status=task.status.value)
        return False
    if ref is not None:
        deps.dispatch.resumes.note(task.id, ref)
    return True


async def _release_grants(deps: QueenDeps, warden_id: WardenId) -> None:
    """Revoke every grant the gone Warden still holds: it draws on none of them any more."""
    for grant in deps.ledger.grants_for(warden_id):
        live = forage_grants.activate(grant) if grant.state is GrantState.ISSUED else grant
        reason = "The Warden's device is gone; nothing draws on its grants."
        await forage_grants.revoke(deps.ledger, deps, live, RevocationCause.HOLDER_OFFLINE, reason)
