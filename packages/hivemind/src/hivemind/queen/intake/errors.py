"""Define the ways a goal request can be refused by its own bookkeeping, on purpose.

A goal request (`hivemind.queen.intake.model.GoalRequest`) is the Queen's durable record of a
goal a human asked for through the Hive Entrance (docs/adr/0040). Three things can go wrong with
one on purpose, and each has its own class so the Entrance can answer with the right status: the
id names no row (`GoalRequestNotFoundError`, a 404), a row with that id already exists
(`GoalRequestExistsError`, a 409), or a caller asks for an edge the request's state machine does
not have (`InvalidGoalRequestTransitionError`, a 409: confirming a request that is not awaiting
confirmation, say). A goal the planner could not plan is not an error here: it is a request that
ends REFUSED with its reason, which is data, not an exception.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Raised by `hivemind.queen.intake.state`, the two stores and `.writes`; caught by
    whichever caller can answer the human (the Hive Entrance). Calls into `hivemind.common.errors`
    and `hivemind.queen.errors` only.

Key invariants:
    - Every class sets its own `code`; none shares a code with another error in the Hive.

See Also:
    - .claude/codingrules.md section 10 for the exceptions rules this module follows.
    - hivemind.queen.intake.state for the transition table InvalidGoalRequestTransitionError
      names an edge of.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import ConflictError, NotFoundError
from hivemind.queen.errors import QueenError

__all__ = [
    "GoalRequestExistsError",
    "GoalRequestNotFoundError",
    "InvalidGoalRequestTransitionError",
]


class GoalRequestNotFoundError(NotFoundError, QueenError):
    """Raise when a goal request id names no row in the Queen's goal-request table."""

    code: ClassVar[str] = "hivemind.queen.goal_request_not_found"

    def __init__(self, request_id: str) -> None:
        """Build the error for an id the table has never held.

        Args:
            request_id: The goal request id a caller asked for.
        """
        super().__init__(f"No goal request {request_id} exists in the Queen's intake table.")
        self.request_id = request_id


class GoalRequestExistsError(ConflictError, QueenError):
    """Raise when a goal request is inserted under an id the table already holds."""

    code: ClassVar[str] = "hivemind.queen.goal_request_exists"

    def __init__(self, request_id: str) -> None:
        """Build the error for a duplicate id; nothing was written.

        Args:
            request_id: The id that is already taken.
        """
        super().__init__(f"Goal request {request_id} already exists; nothing was written.")
        self.request_id = request_id


class InvalidGoalRequestTransitionError(ConflictError, QueenError):
    """Raise when a goal request is asked to move along an edge its state machine does not have."""

    code: ClassVar[str] = "hivemind.queen.invalid_goal_request_transition"

    def __init__(self, request_id: str, from_state: str, to_state: str) -> None:
        """Build the error for a forbidden goal request transition.

        Args:
            request_id: The goal request that was asked to move.
            from_state: The state it is in (a `GoalRequestState` value, or "NEW" for an insert).
            to_state: The state a caller asked for.
        """
        super().__init__(
            f"Goal request {request_id} cannot move from {from_state} to {to_state}: no such "
            "edge exists in the goal request state machine."
        )
        self.request_id = request_id
        self.from_state = from_state
        self.to_state = to_state
