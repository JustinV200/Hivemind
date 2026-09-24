"""Define the intake half of the Queen's tick: she plans every durable goal request herself.

Docs/adr/0032, "A goal is durable before it is acknowledged": the Hive Entrance commits a goal
request and wakes the Queen, and `drain_goal_requests` is what that wake leads to, on every tick.
It settles the request she was planning (`PlanningLane`: one plan at a time, run beside her tick
because a plan is one long model call that would otherwise hold every Warden's Heartbeat unread),
settles any PLANNING row a crash left behind (PLANNED when its goal's root task is already in the
Brood Chamber, found by the request id every task carries, otherwise planned again, so nothing
is lost and nothing is planned twice), holds a request that must be echoed back first
(AWAITING_CONFIRMATION, with the echo as a chat notice), starts the next RECEIVED one planning,
and tells a request's device once every task of its goal is terminal. A plan runs
`plan_goal_graph` with the request's budget, tier, origin, device, ceiling and id; a plan the
planner or its model cannot produce refuses the request with its reason instead of failing the
Queen. Nothing is planned while her own model is clustered: the rows wait, durable.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen`'s own tick (`drain_goal_requests`) and stop
    (`stop_planning`). Calls into `hivemind.brood_chamber`, `hivemind.common.tasks`,
    `hivemind.llm.errors`, `hivemind.queen.chat` (post_notice), `hivemind.queen.cluster`
    (awake_available), `hivemind.queen.deps`, `hivemind.queen.goal_submission`,
    `hivemind.queen.intake`, `hivemind.queen.planner` (PlannerError) and waggle only.

Key invariants:
    - At most one plan is in flight; its task is owned by `deps.planning`, reaped by the first
      tick after it finishes (surfacing any unexpected error there) or by `stop_planning`.
    - A PLANNING row is re-planned only when no plan is in flight for it and its goal is absent
      from the Brood Chamber, so a request is planned exactly once.
    - Every refusal's words stay on the request row (C2); the trail and the logs get its code.
    - Every `HumanChannel` call follows the durable write it reports, so a device is never told
      of a state the rows do not hold; a crash in between loses the push, never the state.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the decision.
    - hivemind.queen.intake for the request, its state machine and every write.
    - hivemind.queen.goal_submission for plan_goal_graph, the plan itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from hivemind.brood_chamber import TaskFilter, is_terminal
from hivemind.common.tasks import reap
from hivemind.llm.errors import LLMError
from hivemind.queen.chat import post_notice
from hivemind.queen.cluster import awake_available
from hivemind.queen.deps import PlanningLane, QueenDeps, WardenLink
from hivemind.queen.goal_submission import GoalTerms, plan_goal_graph
from hivemind.queen.intake import (
    GoalRequest,
    GoalRequestQuery,
    GoalRequestState,
    Refusal,
    hold,
    mark_finished,
    mark_planned,
    refuse,
    start_planning,
)
from hivemind.queen.planner import PlannerError
from waggle.ids import TaskId

MAX_REQUESTS_PER_TICK = 20  # A page of RECEIVED rows looked at per tick; the rest wait in order.
MAX_GOALS_CHECKED_PER_TICK = 50  # Planned goals checked for completion per tick, oldest first.
# The failures that refuse a request rather than fail the Queen: the plan could not become a
# valid graph, or the planner's model failed on every rung and every binding of its chain.
_PLANNING_FAILURES: tuple[type[Exception], ...] = (PlannerError, LLMError)

__all__ = [
    "MAX_GOALS_CHECKED_PER_TICK",
    "MAX_REQUESTS_PER_TICK",
    "drain_goal_requests",
    "stop_planning",
]


async def drain_goal_requests(deps: QueenDeps, wardens: Sequence[WardenLink]) -> None:
    """Settle, hold, plan and announce goal requests: the intake half of one tick.

    Args:
        deps: The Queen's collaborators; `goal_requests`, `planning` and `chamber` are read.
        wardens: Every Warden attached now; a plan started this tick describes their Cells.

    Raises:
        Exception: Whatever unexpected error the last finished plan raised (never a refusal,
            which is settled on its row), surfaced here so the tick owns it.
    """
    await _reap_finished_plan(deps.planning)
    thinking = awake_available(deps.cluster_state, deps)
    await _settle_leftovers(deps, wardens, thinking=thinking)
    await _hold_or_plan(deps, wardens, thinking=thinking)
    await _announce_finished_goals(deps)


async def stop_planning(lane: PlanningLane) -> None:
    """Cancel and reap the plan in flight, if any; its row stays PLANNING for the next start.

    Args:
        lane: The Queen's planning lane (`deps.planning`).
    """
    task = lane.task
    lane.task, lane.request_id = None, None
    if task is not None:
        await reap(task)


async def _reap_finished_plan(lane: PlanningLane) -> None:
    """Free the lane once its plan is done, re-raising anything the plan did not settle."""
    task = lane.task
    if task is None or not task.done():
        return  # Nothing in flight, or still planning beside this tick.
    lane.task, lane.request_id = None, None
    # result(), not a bare discard: an unexpected failure belongs to the tick, never swallowed.
    task.result()


async def _settle_leftovers(
    deps: QueenDeps, wardens: Sequence[WardenLink], *, thinking: bool
) -> None:
    """Settle every PLANNING row no plan is in flight for: a crash left it mid-plan."""
    leftovers = await deps.goal_requests.list_requests(
        GoalRequestQuery(state=GoalRequestState.PLANNING, limit=MAX_REQUESTS_PER_TICK)
    )
    for request in leftovers:
        if request.id == deps.planning.request_id:
            continue  # Being planned right now, beside this tick; its own plan settles it.
        goal_id = await _planned_goal_id(deps, request.id)
        if goal_id is not None:
            # The graph was persisted before the crash: never plan the same request twice.
            planned = await mark_planned(deps, request, goal_id)
            await deps.human_channel.goal_request_planned(planned)
        elif thinking and deps.planning.task is None:
            _start_plan(deps, wardens, request)


async def _hold_or_plan(deps: QueenDeps, wardens: Sequence[WardenLink], *, thinking: bool) -> None:
    """Hold every RECEIVED request that must be echoed first; start the next one planning."""
    received = await deps.goal_requests.list_requests(
        GoalRequestQuery(state=GoalRequestState.RECEIVED, limit=MAX_REQUESTS_PER_TICK)
    )
    for request in received:
        if request.needs_confirmation and request.confirmed_at is None:
            await _hold(deps, request)
        elif thinking and deps.planning.task is None:
            # PLANNING is committed before the planner is ever called, so a crash from here on
            # is settled by _settle_leftovers on the next start, never planned from scratch.
            _start_plan(deps, wardens, await start_planning(deps, request))


async def _hold(deps: QueenDeps, request: GoalRequest) -> None:
    """Hold `request` for the human's yes, echoing its words back in the chat first."""
    # Echo, then hold: a crash between the two echoes it again on the next start (twice is
    # harmless), where the other order could leave a spoken goal held with no echo at all.
    echo = f"Before I plan it, please confirm this goal: {request.text}"
    await post_notice(deps, echo, ref=request.id, task_id=None)
    await deps.human_channel.goal_request_held(await hold(deps, request))


def _start_plan(deps: QueenDeps, wardens: Sequence[WardenLink], request: GoalRequest) -> None:
    """Start planning a PLANNING request in the lane, beside the tick (module docstring)."""
    lane = deps.planning
    lane.request_id = request.id
    # Owned by the lane (codingrules 11): reaped by the first tick after it finishes, or by
    # stop_planning; never a bare task whose handle is dropped.
    lane.task = asyncio.ensure_future(_plan(deps, tuple(wardens), request))


async def _plan(deps: QueenDeps, wardens: Sequence[WardenLink], request: GoalRequest) -> None:
    """Plan one request into its goal, then settle the row PLANNED or REFUSED and say so."""
    try:
        goal_id = await plan_goal_graph(deps, wardens, request.text, _terms(request))
    except _PLANNING_FAILURES as exc:
        refused = await refuse(deps, request, _refusal(exc))
        why = f"I could not plan your goal: {refused.refusal}"
        await post_notice(deps, why, ref=request.id, task_id=None)
        await deps.human_channel.goal_request_refused(refused)
    else:
        planned = await mark_planned(deps, request, goal_id)
        await deps.human_channel.goal_request_planned(planned)
    finally:
        # Wake the tick at once, so it reaps this plan and places the new goal's ready tasks.
        deps.wake.set()


async def _announce_finished_goals(deps: QueenDeps) -> None:
    """Tell a request's device, once, that every task of its planned goal is terminal."""
    open_goals = await deps.goal_requests.list_requests(
        GoalRequestQuery(
            state=GoalRequestState.PLANNED, unfinished=True, limit=MAX_GOALS_CHECKED_PER_TICK
        )
    )
    for request in open_goals:
        tasks = await deps.chamber.list(TaskFilter(goal_id=request.goal_id))
        if tasks and all(is_terminal(task.status) for task in tasks):
            finished = await mark_finished(deps, request)
            await deps.human_channel.goal_finished(finished)


async def _planned_goal_id(deps: QueenDeps, request_id: str) -> TaskId | None:
    """Return the goal id already planned for `request_id`, or None when none was persisted."""
    tasks = await deps.chamber.list(TaskFilter(goal_request_id=request_id))
    # A goal's root task is the one whose id is its goal id; the graph is persisted at once.
    return next((task.id for task in tasks if task.id == task.goal_id), None)


def _terms(request: GoalRequest) -> GoalTerms:
    """Build the terms a request's goal is planned under: all of it but its words."""
    return GoalTerms(
        clearance=request.clearance,
        capabilities=request.capabilities,
        origin=request.origin,
        comb_shield=request.comb_shield,
        spend_cap_usd=request.budget_usd,
        device_id=request.device_id,
        goal_request_id=request.id,
    )


def _refusal(error: Exception) -> Refusal:
    """Turn a planning failure into a refusal: its words for the human, its class for the trail."""
    name = type(error).__name__
    return Refusal(reason=str(error) or name, code=name)
