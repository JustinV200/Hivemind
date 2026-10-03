"""Define requeue_unfinished: give back every task a stopping Hive leaves on its Cells.

A Hive that stops (`hive run` interrupted or out of time, `hive serve` shut down) takes every Cell
it held along: the Hive Stand's lease is released and every Virtual Cell retired
(`hivemind.cli.compose.hive.run`). A task still ASSIGNED, RUNNING or BLOCKED on one of them can
never finish there, and its Warden, minted for this process, never comes back. Left that way, no
later start would place it again (only a PENDING task is ready), and its question, still in every
inbox, could be answered into a task no bee runs. `requeue_unfinished` gives each one back once the
Cells are gone: a BLOCKED task's question is withdrawn first (BLOCKED -> RUNNING, the one way out
short of cancelling, as an isolation's hold takes it), then `unassign` makes it PENDING as a new
attempt, unplaced and unbound, so the next start places it afresh and its fresh bee asks again.

That is how the Hive already treats the work a stop interrupts: tasks survive a stop and are read
back, a bee's work resumes from its Handoff, a task stranded ASSIGNED is placed afresh by the next
pass, a goal request left PLANNING is settled by the next start, and a follow that ends (a
timeout) never ends the goal. A stop is no decision about the goal, so nothing is cancelled for
one; cancelling stays a human's or the Queen's decision, or the end of a task that can never run.
A task bound to a Night Veil Cell is the one such task: that Cell is teardown-only (codingrules
8.7), so a stop ends the task with it, as the Cell's teardown purge does when it runs. A PAUSED
task is left alone: its hold (Clustering, a quarantine, an isolation) is someone's to lift.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.dispatcher.ready`.
    Called by `hivemind.cli.compose.hive.run.run_hive` once the Queen and Warden have stopped and
    every Cell is retired. Calls into `hivemind.brood_chamber`, `hivemind.cell` (the tier enum),
    `hivemind.common.logging`, `hivemind.queen.deps` and waggle only.

Key invariants:
    - Only a task placed on a Warden this Queen placed work on (`DispatchBook.placed_on`) is
      touched: another Queen's work on the same Brood Chamber is never moved.
    - No task it reaches is left ASSIGNED, RUNNING or BLOCKED: each ends PENDING (unplaced, its
      attempt advanced) or, on a Night Veil Cell, CANCELLED; a BLOCKED one's question WITHDRAWN.
    - A task another process moved meanwhile is read again and given back as it now stands (a
      question answered from `hive inbox answer` leaves it RUNNING, still owed back); one that
      finished, or left the chamber, is left alone, and never stops the others.

See Also:
    - hivemind.brood_chamber.chamber.lifecycle for unassign, the edge back to PENDING.
    - hivemind.queen.dispatcher.ready.dispatch for the stranded-ASSIGNED recovery this mirrors.
    - hivemind.brood_chamber.chamber.night_veil for the purge's own cancellation.
"""

from __future__ import annotations

from collections.abc import Collection

from hivemind.brood_chamber import (
    InvalidTransitionError,
    QuestionStatus,
    Task,
    TaskFilter,
    TaskNotFoundError,
    TaskStatus,
)
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.cell import CombShieldLevel
from hivemind.common.logging import get_logger
from hivemind.queen.deps import QueenDeps
from waggle.ids import MessageId, TaskId, WardenId

# Why a task is given back, on its `task.unassigned` and its question's withdrawal alike: plain
# words and no content (codingrules section 12).
STOPPED_REASON = (
    "The Hive stopped under it; its Cell and Warden are gone, so it is placed afresh when the "
    "Hive starts again."
)
# Why a task on a Night Veil Cell is cancelled instead (the purge's own reason, in other words).
VEILED_REASON = (
    "The Hive stopped under it on a Night Veil Cell, and nothing of that Cell outlives it."
)
# The statuses a stop can catch a task on a Cell in; PAUSED is a hold, and is never moved here.
_PLACED = (TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED)
_READS = 2  # One read, and one more for a task another process moved under the first write.

log = get_logger(__name__)

__all__ = ["STOPPED_REASON", "VEILED_REASON", "requeue_unfinished"]


async def requeue_unfinished(deps: QueenDeps) -> tuple[TaskId, ...]:
    """Give back every task still placed on one of this Queen's Wardens, once her Cells are gone.

    Args:
        deps: The stopped Queen's collaborators; `dispatch.placed_on` names her Wardens.

    Returns:
        The tasks given back to PENDING, in the order found; a Night Veil task cancelled
        instead, and one that moved on meanwhile, are not among them.
    """
    placed_on = frozenset(deps.dispatch.placed_on)
    if not placed_on:
        return ()  # She placed nothing, so nothing of hers can be left on a Cell.
    requeued: list[TaskId] = []
    # Under the dispatch lock, like every write that moves a task in or out of PENDING.
    async with deps.dispatch.lock:
        for task in await _placed_on(deps, placed_on):
            if await _give_back(deps, task.id):
                requeued.append(task.id)
    if requeued:
        log.info("queen.unfinished_requeued", tasks=len(requeued))
    return tuple(requeued)


async def _placed_on(deps: QueenDeps, wardens: Collection[WardenId]) -> tuple[Task, ...]:
    """Return every task in a placed status whose Warden is one of `wardens`."""
    found: list[Task] = []
    # The chamber filters by status, not by Warden; a placed task's row names its Warden.
    for status in _PLACED:
        query = TaskFilter(status=status, limit=MAX_TASK_FILTER_LIMIT)
        # Latency: one local indexed read of the Brood Chamber per status.
        found.extend(task for task in await deps.chamber.list(query) if task.warden_id in wardens)
    return tuple(found)


async def _give_back(deps: QueenDeps, task_id: TaskId) -> bool:
    """Give `task_id` back as it stands now, reading it again if it moves under a write."""
    # Another process (`hive inbox answer`) may move it between the read and the write: one more
    # read then sees where it went, and an answered question leaves it RUNNING, still owed back.
    for _ in range(_READS):
        try:
            return await _give_back_as_read(deps, await deps.chamber.get(task_id))
        except InvalidTransitionError:
            log.debug("queen.requeue_moved_on", task_id=task_id)
        except TaskNotFoundError:
            return False  # Gone from the chamber: nothing left to give back.
    return False


async def _give_back_as_read(deps: QueenDeps, task: Task) -> bool:
    """Withdraw `task`'s question if it waits on one, then make it PENDING; True when it is."""
    if task.status not in _PLACED:
        return False  # Finished or given back meanwhile: it stands as it is.
    if task.status is TaskStatus.BLOCKED and task.pending_question_id is not None:
        task = await _withdraw(deps, task.pending_question_id)
    if task.bound_tier is CombShieldLevel.NIGHT_VEIL:
        # Teardown-only (module docstring): ended with its Cell, never placed again.
        await deps.chamber.cancel(task.id, VEILED_REASON)
        return False
    await deps.chamber.unassign(task.id, STOPPED_REASON)
    return True


async def _withdraw(deps: QueenDeps, question_id: MessageId) -> Task:
    """Withdraw one question and tell the human's devices; return its task, RUNNING again."""
    task = await deps.chamber.withdraw(question_id, STOPPED_REASON)
    # The fresh bee asks again on the next start; until then no device should offer this one.
    await deps.human_channel.question_closed(question_id, QuestionStatus.WITHDRAWN)
    return task
