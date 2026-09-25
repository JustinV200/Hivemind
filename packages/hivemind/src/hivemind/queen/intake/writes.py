"""Define the functions that create and move a goal request, each with its own trail event.

A goal request (`hivemind.queen.intake.model.GoalRequest`) changes only through this module: every
function checks its edge against `hivemind.queen.intake.state`'s one transition table, builds the
request's next value (validated again, so an invariant can never be skipped), and hands the row
and its `queen.goal_request_*` event to the `GoalRequestStore`, which commits both together
(codingrules Appendix C). `receive` commits a fresh request (the Hive Entrance answers `202` only
after it returns); `hold`, `confirm`, `decline`, `start_planning`, `mark_planned` and `refuse` are
the machine's edges; `mark_finished` records, once, that every task of a planned goal reached a
terminal status, so its device is told once. The trail never carries the request's words or a
refusal's words, only ids, enum values and a short reason code (codingrules section 12).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Called by `Queen.request_goal`/`confirm_goal_request`/`decline_goal_request`
    (`hivemind.queen.chat.door`) and by the Queen's own intake drain (`hivemind.queen.ticks.
    intake`). Calls into `hivemind.queen.trail` (queen_event), the intake package's own model,
    state and errors, and waggle only; `QueenDeps` only for its type.

Key invariants:
    - Every write goes through `deps.goal_requests` with exactly one event, built here.
    - A request is created only in RECEIVED, never confirmed and never settled.
    - Every edge is checked against the stored row under `deps.intake_lock`, so a caller holding
      a stale copy gets `InvalidGoalRequestTransitionError`, never a lost update.

See Also:
    - hivemind.queen.intake.state for the transition table every edge here is checked against.
    - hivemind.queen.intake.protocol for GoalRequestStore, the store these functions write through.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.queen.intake.errors import InvalidGoalRequestTransitionError
from hivemind.queen.intake.model import MAX_REFUSAL_CHARS, GoalRequest
from hivemind.queen.intake.state import GoalRequestState, assert_goal_request_transition
from hivemind.queen.trail import queen_event
from waggle.ids import TaskId

if TYPE_CHECKING:
    # Only for the type hints: hivemind.queen.deps imports this package to name its store, so a
    # real import here would cycle back through queen/deps.py.
    from hivemind.queen.deps import QueenDeps

_NEW = "NEW"  # How an insert names the state a request comes from, in a transition error.
_RECEIVED_KIND = "queen.goal_request_received"  # The one kind an insert records.
_FINISHED_KIND = "queen.goal_request_finished"  # Recorded once, when a planned goal finishes.
# One kind per edge, keyed by the state the edge arrives in: each target state has exactly one
# edge into it (RECEIVED's only way back in is a confirmation), so the target names the edge.
_EDGE_KINDS: Mapping[GoalRequestState, str] = MappingProxyType(
    {
        GoalRequestState.AWAITING_CONFIRMATION: "queen.goal_request_held",
        GoalRequestState.RECEIVED: "queen.goal_request_confirmed",
        GoalRequestState.PLANNING: "queen.goal_request_planning",
        GoalRequestState.PLANNED: "queen.goal_request_planned",
        GoalRequestState.REFUSED: "queen.goal_request_refused",
    }
)
DECLINED_CODE = "declined"  # The reason code of a request the human declined when it was echoed.

__all__ = [
    "DECLINED_CODE",
    "Refusal",
    "confirm",
    "decline",
    "hold",
    "mark_finished",
    "mark_planned",
    "receive",
    "refuse",
    "start_planning",
]


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a goal request will never be planned: words for the human, a code for the trail.

    Attributes:
        reason: For the human, stored on the row (C2, the Queen's own table); truncated to the
            row's own bound. Never on the trail: it may quote the goal.
        code: A short, stable label for the trail (an error class name, or `DECLINED_CODE`).
    """

    reason: str
    code: str


async def receive(deps: QueenDeps, request: GoalRequest) -> GoalRequest:
    """Commit a fresh RECEIVED request together with its `queen.goal_request_received` event.

    Args:
        deps: The Queen's collaborators; `goal_requests` is the store written to.
        request: A request built by the Hive Entrance (or a test): RECEIVED, never confirmed.

    Returns:
        `request`, once its row and event are committed.

    Raises:
        InvalidGoalRequestTransitionError: `request` is not in RECEIVED, or already confirmed.
        GoalRequestExistsError: Its id is already taken; nothing was written.
    """
    # A fresh row starts at the machine's one entry state; anything else would let a caller
    # skip an edge (arrive PLANNED with a goal id nobody planned, say).
    if request.state is not GoalRequestState.RECEIVED or request.confirmed_at is not None:
        raise InvalidGoalRequestTransitionError(request.id, _NEW, request.state.value)
    event = queen_event(
        deps,
        _RECEIVED_KIND,
        deps.identity.hive_id,
        goal_request_id=request.id,
        origin=request.origin.value,
        source=request.source.value,
        device_id=request.device_id,
        comb_shield=request.comb_shield.value if request.comb_shield is not None else None,
        has_budget=request.budget_usd is not None,
        needs_confirmation=request.needs_confirmation,
    )
    await deps.goal_requests.insert(request, event)
    return request


async def hold(deps: QueenDeps, request: GoalRequest) -> GoalRequest:
    """Move `request` RECEIVED -> AWAITING_CONFIRMATION: it is echoed back, not yet planned."""
    return await _move(deps, request, GoalRequestState.AWAITING_CONFIRMATION, {}, {})


async def confirm(deps: QueenDeps, request_id: str) -> GoalRequest:
    """Move a held request AWAITING_CONFIRMATION -> RECEIVED, so the Queen plans it next.

    Args:
        deps: The Queen's collaborators.
        request_id: The request the human confirmed.

    Returns:
        The request, RECEIVED again, with `confirmed_at` set.

    Raises:
        GoalRequestNotFoundError: No such request exists.
        InvalidGoalRequestTransitionError: It is not awaiting confirmation.
    """
    request = await deps.goal_requests.get(request_id)
    update: dict[str, object] = {"confirmed_at": deps.clock.now()}
    return await _move(deps, request, GoalRequestState.RECEIVED, update, {})


async def decline(deps: QueenDeps, request_id: str, reason: str) -> GoalRequest:
    """Move a held request AWAITING_CONFIRMATION -> REFUSED: the human said no.

    Args:
        deps: The Queen's collaborators.
        request_id: The request the human declined.
        reason: Why, in the human's own words or the Entrance's, stored on the row.

    Returns:
        The request, REFUSED.

    Raises:
        GoalRequestNotFoundError: No such request exists.
        InvalidGoalRequestTransitionError: It is not awaiting confirmation.
    """
    request = await deps.goal_requests.get(request_id)
    # Only a request held for the human's yes can be declined: the table's PLANNING -> REFUSED
    # edge belongs to the planner, and a request already being planned has left the human's hands.
    if request.state is not GoalRequestState.AWAITING_CONFIRMATION:
        raise InvalidGoalRequestTransitionError(
            request.id, request.state.value, GoalRequestState.REFUSED.value
        )
    return await refuse(deps, request, Refusal(reason=reason, code=DECLINED_CODE))


async def start_planning(deps: QueenDeps, request: GoalRequest) -> GoalRequest:
    """Move `request` RECEIVED -> PLANNING, before the planner is ever called."""
    return await _move(deps, request, GoalRequestState.PLANNING, {}, {})


async def mark_planned(deps: QueenDeps, request: GoalRequest, goal_id: TaskId) -> GoalRequest:
    """Move `request` PLANNING -> PLANNED, naming the goal its plan became.

    Args:
        deps: The Queen's collaborators.
        request: The request being planned.
        goal_id: The goal's own id: the first task minted from its plan.

    Returns:
        The request, PLANNED.
    """
    update: dict[str, object] = {"goal_id": goal_id}
    return await _move(deps, request, GoalRequestState.PLANNED, update, {"goal_id": goal_id})


async def refuse(deps: QueenDeps, request: GoalRequest, refusal: Refusal) -> GoalRequest:
    """Move `request` to REFUSED: from PLANNING (it could not be planned) or AWAITING_CONFIRMATION.

    Args:
        deps: The Queen's collaborators.
        request: The request to refuse.
        refusal: The human's reason (on the row only) and the trail's code.

    Returns:
        The request, REFUSED.
    """
    update: dict[str, object] = {"refusal": refusal.reason[:MAX_REFUSAL_CHARS] or refusal.code}
    payload: dict[str, JsonValue] = {"reason_code": refusal.code}
    return await _move(deps, request, GoalRequestState.REFUSED, update, payload)


async def mark_finished(deps: QueenDeps, request: GoalRequest) -> GoalRequest:
    """Record, once, that every task of `request`'s planned goal is terminal.

    Args:
        deps: The Queen's collaborators.
        request: A PLANNED request whose goal just finished.

    Returns:
        The request with `finished_at` set.
    """
    finished = _next_value(deps, request, {"finished_at": deps.clock.now()})
    event = queen_event(
        deps,
        _FINISHED_KIND,
        deps.identity.hive_id,
        goal_request_id=request.id,
        goal_id=request.goal_id,
    )
    await deps.goal_requests.update(finished, event)
    return finished


async def _move(
    deps: QueenDeps,
    request: GoalRequest,
    to_state: GoalRequestState,
    update: dict[str, object],
    payload: dict[str, JsonValue],
) -> GoalRequest:
    """Check the edge from the row as it stands, build the next value, write it with its event."""
    # One edge at a time, from the stored row rather than the caller's copy: the Queen's intake,
    # a plan finishing beside her tick and a revocation through the Hive Entrance may all move one
    # request, and a stale copy must fail its edge instead of overwriting a newer state.
    async with deps.intake_lock:
        # Latency: one local primary-key read of the Queen's goal-request table.
        current = await deps.goal_requests.get(request.id)
        assert_goal_request_transition(current.state, to_state, current.id)
        moved = _next_value(deps, current, {**update, "state": to_state})
        event = queen_event(
            deps,
            _EDGE_KINDS[to_state],
            deps.identity.hive_id,
            goal_request_id=current.id,
            from_state=current.state.value,
            **payload,
        )
        # Latency: one local transaction writing the row and its event together.
        await deps.goal_requests.update(moved, event)
    return moved


def _next_value(deps: QueenDeps, request: GoalRequest, update: dict[str, object]) -> GoalRequest:
    """Build `request`'s next value, validated again, with `updated_at` advanced to now."""
    # model_validate, never model_copy: the model's own cross-field invariants must hold for
    # every value that reaches the store, whichever edge produced it.
    fields = {**request.model_dump(), **update, "updated_at": deps.clock.now()}
    return GoalRequest.model_validate(fields)
