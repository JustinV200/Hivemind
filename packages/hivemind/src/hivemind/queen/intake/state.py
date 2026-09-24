"""Define GoalRequestState and the one transition table a goal request moves through.

A goal request is durable before it is acknowledged (docs/adr/0032): the Hive Entrance commits it
RECEIVED and answers `202`, and from then on only the Queen moves it. She plans a RECEIVED request
(PLANNING, then PLANNED with its goal id, or REFUSED with a reason), or, when the request must be
echoed back first (a spoken goal, or one held for a human's step-up, ADR-0033), holds it in
AWAITING_CONFIRMATION until the human confirms it (back to RECEIVED) or declines it (REFUSED).
PLANNED and REFUSED are terminal: a request is planned at most once, and a refused one is asked
again as a new request. This module is that machine's one table (codingrules section 9), each edge
commented with who takes it; `hivemind.queen.intake.writes` is the only code that moves a request,
always through `assert_goal_request_transition`, and every edge is a `queen.goal_request_*` trail
event written in the same transaction as the row (codingrules Appendix C).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Read by `hivemind.queen.intake.model` (the `state` field) and `.writes`. Calls
    into `hivemind.queen.intake.errors` only.

Key invariants:
    - TRANSITIONS has exactly one entry per GoalRequestState; PLANNED and REFUSED map to nothing.
    - RECEIVED is the only state a request is created in; there is no edge back out of PLANNING
      except PLANNED or REFUSED, so a request that began planning is never planned twice.

See Also:
    - .claude/codingrules.md Appendix C for the "Goal request" row this table implements.
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the states.
    - hivemind.queen.intake.writes for the one mover.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from hivemind.queen.intake.errors import InvalidGoalRequestTransitionError

__all__ = [
    "GOAL_REQUEST_TRANSITIONS",
    "TERMINAL_GOAL_REQUEST_STATES",
    "GoalRequestState",
    "assert_goal_request_transition",
    "can_goal_request_transition",
]


class GoalRequestState(Enum):
    """Where one goal request is: waiting, held for the human, being planned, or settled."""

    RECEIVED = "RECEIVED"  # Committed and waiting for the Queen to plan it (or to hold it).
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # Echoed back; waits on the human's yes.
    PLANNING = "PLANNING"  # The Queen is planning it now, or was when a crash stopped her.
    PLANNED = "PLANNED"  # Terminal: its task graph is persisted; `goal_id` names the goal.
    REFUSED = "REFUSED"  # Terminal: it will never be planned; `refusal` says why.


# The single transition table (codingrules section 9): one entry per state, each edge commented
# with who or what takes it. Read-only, so no caller can widen the machine at runtime.
GOAL_REQUEST_TRANSITIONS: Mapping[GoalRequestState, frozenset[GoalRequestState]] = MappingProxyType(
    {
        GoalRequestState.RECEIVED: frozenset(
            {
                GoalRequestState.PLANNING,  # the Queen's intake drain starts planning it
                GoalRequestState.AWAITING_CONFIRMATION,  # it must be echoed back first
            }
        ),
        GoalRequestState.AWAITING_CONFIRMATION: frozenset(
            {
                GoalRequestState.RECEIVED,  # the human confirmed it; plan it next
                GoalRequestState.REFUSED,  # the human declined it
            }
        ),
        GoalRequestState.PLANNING: frozenset(
            {
                GoalRequestState.PLANNED,  # its graph is persisted (or found after a crash)
                GoalRequestState.REFUSED,  # the planner or its model could not plan it
            }
        ),
        GoalRequestState.PLANNED: frozenset(),  # terminal: nothing follows
        GoalRequestState.REFUSED: frozenset(),  # terminal: nothing follows
    }
)

# The states nothing follows; cross-checked against the table by a test rather than derived from
# it, so a new terminal state has to be named on purpose in both places.
TERMINAL_GOAL_REQUEST_STATES: frozenset[GoalRequestState] = frozenset(
    {GoalRequestState.PLANNED, GoalRequestState.REFUSED}
)


def can_goal_request_transition(from_state: GoalRequestState, to_state: GoalRequestState) -> bool:
    """Return whether the table allows moving a goal request from `from_state` to `to_state`.

    Args:
        from_state: The request's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges the table lists for `from_state`.
    """
    return to_state in GOAL_REQUEST_TRANSITIONS[from_state]


def assert_goal_request_transition(
    from_state: GoalRequestState, to_state: GoalRequestState, request_id: str
) -> None:
    """Raise unless the table allows moving goal request `request_id` to `to_state`.

    Args:
        from_state: The request's current state.
        to_state: The state a caller wants to move it to.
        request_id: The request's id, folded into the error message.

    Raises:
        InvalidGoalRequestTransitionError: The table lists no such edge, for instance moving a
            PLANNED request anywhere, or confirming one that is not AWAITING_CONFIRMATION.
    """
    if not can_goal_request_transition(from_state, to_state):
        raise InvalidGoalRequestTransitionError(request_id, from_state.value, to_state.value)
