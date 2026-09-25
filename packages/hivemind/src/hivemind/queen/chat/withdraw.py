"""Withdraw what a revoked device asked for: refuse its unplanned requests, stop its goals.

Revoking a device at the Hive Entrance (roadmap 10.5d, ADR-0041) ends its sessions at once, but
the work it asked for lives in the Queen's own tables, so the Entrance asks her to withdraw it
rather than writing them itself. `refuse_unplanned` refuses every goal request the device
submitted that has not been planned yet (RECEIVED, AWAITING_CONFIRMATION or PLANNING), so a
revoked device's work never starts; a request whose plan is in flight is refused too, and the plan
finishing later stops the goal it produced (`hivemind.queen.ticks.intake`), while a goal its plan
already persisted (the plan landed, or a crash left it) is stopped here at once. `stop_goal` ends a
planned goal: a task not yet placed is cancelled in the Brood Chamber, and a placed one is first
stopped on its Warden with a `TaskCancel` (the Warden relays it to the sub-bee running the task,
`hivemind.wardens.ticks.control`, which checkpoints within its grace period and is killed), then
cancelled in the chamber, which is how a cancellation is recorded: the Queen decides it and writes
`task.cancelled` herself, and whatever the Warden reports about that task afterwards is a stale
echo her autopilot only records (`hivemind.queen.autopilot.table`). A placed task whose Warden is
not attached cannot be stopped, so it is left running and the goal is reported unfinished.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package, beside the door that calls it (`ChatDoor.refuse_device_requests`,
    `ChatDoor.cancel_goal`); `stop_goal` is also called by her intake tick for a goal planned
    for a request refused meanwhile. Calls into `hivemind.brood_chamber`, `hivemind.queen.intake`
    and waggle only; `QueenDeps` and `WardenLink` only for their types.

Key invariants:
    - A request is refused only from an unplanned state, through the intake's own edge (checked
      against the stored row), and its device is told as for any refusal.
    - A placed task is cancelled in the chamber only once its Warden has been sent the TaskCancel.
    - Nothing here waits on a Warden's answer: the order is sent, and the chamber records it.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for revocation.
    - hivemind.entrance.runtime.seams for QueenGoalLedger, the Entrance's caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING

from hivemind.brood_chamber import (
    InvalidTransitionError,
    QuestionStatus,
    Task,
    TaskFilter,
    TaskStatus,
    is_terminal,
)
from hivemind.common.logging import get_logger
from hivemind.queen.intake import (
    MAX_REQUEST_PAGE,
    GoalRequest,
    GoalRequestId,
    GoalRequestQuery,
    GoalRequestState,
    InvalidGoalRequestTransitionError,
    Refusal,
    refuse,
)
from waggle.envelope import wrap
from waggle.errors import TransportError
from waggle.ids import DeviceId, TaskId, WardenId
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.task import TaskCancel

if TYPE_CHECKING:
    # Only for the annotations: hivemind.queen.deps imports this package, so a real import cycles.
    from hivemind.queen.deps import QueenDeps, WardenLink

REVOKED_CODE = "device_revoked"  # The trail's code: its device was revoked before it was planned.
CANCEL_GRACE_S = 10.0  # Seconds a cancelled bee gets to checkpoint before its Warden kills it.
CANCEL_SEND_TIMEOUT_S = 5.0  # One TaskCancel onto a Warden's link; a stuck link leaves it running.
# The states a request can still be refused from: nothing of it has been planned into work yet.
_UNPLANNED = (
    GoalRequestState.RECEIVED,
    GoalRequestState.AWAITING_CONFIRMATION,
    GoalRequestState.PLANNING,
)

log = get_logger(__name__)

__all__ = [
    "CANCEL_GRACE_S",
    "CANCEL_SEND_TIMEOUT_S",
    "REVOKED_CODE",
    "refuse_unplanned",
    "stop_goal",
]


async def refuse_unplanned(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], device_id: DeviceId, reason: str
) -> tuple[GoalRequestId, ...]:
    """Refuse every goal request `device_id` submitted that has not been planned yet.

    Args:
        deps: The Queen's collaborators; `goal_requests` is read and moved.
        wardens: Every attached Warden's link, by id: where a goal already persisted is stopped.
        device_id: The device being revoked.
        reason: For the human, kept on each refused row (never on the trail).

    Returns:
        The requests refused, oldest first within each state; one planned or refused meanwhile
        is left out.
    """
    refused: list[GoalRequestId] = []
    # State by state: each list is a snapshot, and a request that moves meanwhile fails its edge.
    for state in _UNPLANNED:
        query = GoalRequestQuery(device_id=device_id, state=state, limit=MAX_REQUEST_PAGE)
        # Latency: one local indexed read of the Queen's goal-request table per state.
        for request in await deps.goal_requests.list_requests(query):
            if not await _refuse_one(deps, request, reason):
                continue
            refused.append(request.id)
            # A plan may already have persisted its goal (it landed, or a crash left it): stop it.
            await _stop_persisted(deps, wardens, request.id, reason)
    return tuple(refused)


async def stop_goal(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], goal_id: TaskId, reason: str
) -> bool:
    """Cancel every unfinished task of a goal, stopping placed ones on their Warden first.

    Args:
        deps: The Queen's collaborators; `chamber` is read and moved.
        wardens: Every attached Warden's link, by id: where a placed task is stopped.
        goal_id: The goal (its first task's id).
        reason: A short phrase each `task.cancelled` event and TaskCancel carries.

    Returns:
        True when no task of the goal is left unfinished; False while one runs on a Warden that
        could not be told to stop.
    """
    # Latency: one local chamber read, then local transactions and one link send per task.
    for task in await deps.chamber.list(TaskFilter(goal_id=goal_id)):
        if not is_terminal(task.status):
            await _stop_task(deps, wardens, task, reason)
    # Wake her tick: a stranded dependant is closed and nothing cancelled is dispatched again.
    deps.wake.set()
    # Latency: one more local chamber read, of what the cancels left.
    remaining = await deps.chamber.list(TaskFilter(goal_id=goal_id))
    return all(is_terminal(task.status) for task in remaining)


async def _refuse_one(deps: QueenDeps, request: GoalRequest, reason: str) -> bool:
    """Refuse one request and tell its device; False when it moved on meanwhile."""
    try:
        refused = await refuse(deps, request, Refusal(reason=reason, code=REVOKED_CODE))
    except InvalidGoalRequestTransitionError:
        # Planned (or settled) between the read and the edge: a planned goal is stop_goal's.
        log.debug("queen.request_moved_on", goal_request_id=request.id)
        return False
    await deps.human_channel.goal_request_refused(refused)
    return True


async def _stop_persisted(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], request_id: str, reason: str
) -> None:
    """Stop the goal a refused request's plan already persisted, if any."""
    # Latency: one local chamber read by the request id every task of its goal carries.
    tasks = await deps.chamber.list(TaskFilter(goal_request_id=request_id))
    # A goal's root task is the one whose id is its goal id; the graph is persisted at once.
    for goal_id in {task.goal_id for task in tasks}:
        await stop_goal(deps, wardens, goal_id, reason)


async def _stop_task(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], task: Task, reason: str
) -> None:
    """Stop one unfinished task: on its Warden when placed, then in the chamber."""
    # A placed task's work runs on its Warden's Cell: it is stopped there before it is recorded.
    if task.warden_id is not None and not await _tell_warden(deps, wardens, task, reason):
        return
    try:
        # A blocked task's question is closed first, so no device is left to answer it.
        if task.status is TaskStatus.BLOCKED and task.pending_question_id is not None:
            await deps.chamber.withdraw(task.pending_question_id, reason)
            await deps.human_channel.question_closed(
                task.pending_question_id, QuestionStatus.WITHDRAWN
            )
        # Latency: one local transaction recording task.cancelled with the task.
        await deps.chamber.cancel(task.id, reason)
    except InvalidTransitionError:
        # It finished between the read and the cancel: its own outcome stands.
        log.debug("queen.task_finished_first", task_id=task.id)


async def _tell_warden(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], task: Task, reason: str
) -> bool:
    """Send the task's Warden a TaskCancel; False when there is no link to send it over."""
    link = wardens.get(WardenId(task.warden_id)) if task.warden_id is not None else None
    if link is None:
        # Its Warden is not attached: nothing can stop the work there, so it is left running.
        log.warning("queen.cancel_unreachable", task_id=task.id, warden_id=task.warden_id)
        return False
    order = TaskCancel(task_id=task.id, grace_s=CANCEL_GRACE_S, reason=reason[:MAX_REASON_CHARS])
    try:
        # External wait: one frame onto the Warden's link, milliseconds; bounded all the same.
        async with asyncio.timeout(CANCEL_SEND_TIMEOUT_S):
            await link.transport.send(wrap(order, link.hop, clock=deps.clock))
    except (TimeoutError, TransportError) as error:
        # A link that cannot take the frame cannot stop the work either: it is left running.
        log.warning("queen.cancel_undelivered", task_id=task.id, error=type(error).__name__)
        return False
    return True
