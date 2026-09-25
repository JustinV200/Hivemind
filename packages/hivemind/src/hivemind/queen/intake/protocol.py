"""Define GoalRequestStore, the persistence seam for the Queen's durable goal requests.

A goal request (`hivemind.queen.intake.model.GoalRequest`) is only useful if it is durable before
the Hive Entrance acknowledges it (docs/adr/0040), and every one of its transitions is a trail
event written in the same transaction as the row (codingrules Appendix C). This protocol is that
seam: `insert` and `update` take the row and its `queen.goal_request_*` event together and commit
both or neither, `get` reads one back by id, and `list_requests` reads a filtered page
(`GoalRequestQuery`: by state, by submitting device, only unfinished goals) oldest first, which is
the order the Queen drains them in. `check_goal_request_event` is the guard both implementations
(`hivemind.queen.intake.memory`, `hivemind.queen.intake.sqlite`) call before writing, so a bug can
never file an event under the wrong request.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Implemented by `.memory` and `.sqlite`; written only through `.writes`; read by
    the Queen's own intake drain (`hivemind.queen.ticks.human.intake`) and, for the goals views, the
    Hive Entrance. Calls into `hivemind.pheromone` (QueenEvent), `hivemind.common.errors` and the
    intake package's own model and state only.

Key invariants:
    - A mutation's event commits together with the row it describes, or neither commits.
    - `list_requests` orders by `(received_at, id)` ascending, so draining is first come, first
      planned, and a restart drains in the same order.

See Also:
    - .claude/codingrules.md Appendix C for the same-transaction rule.
    - hivemind.brood_chamber.store.protocol for TaskStore, the pattern this protocol mirrors.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone import QueenEvent
from hivemind.queen.intake.model import GoalRequest
from hivemind.queen.intake.state import GoalRequestState
from waggle.messages.base import DeviceIdField, UtcDatetime

DEFAULT_REQUEST_PAGE = 100  # Generous for a drain or a device's own goals view; still bounded.
MAX_REQUEST_PAGE = 1_000  # Matches the order of TaskFilter's own ceiling; never unbounded.
REQUEST_EVENT_PREFIX = "queen.goal_request_"  # Every goal request edge's kind starts with this.

__all__ = [
    "DEFAULT_REQUEST_PAGE",
    "MAX_REQUEST_PAGE",
    "REQUEST_EVENT_PREFIX",
    "GoalRequestQuery",
    "GoalRequestStore",
    "check_goal_request_event",
]


class GoalRequestQuery(BaseModel):
    """A filtered, bounded read of the goal-request table; every unset field applies no filter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: GoalRequestState | None = Field(default=None, description="Only requests in this state.")
    device_id: DeviceIdField | None = Field(
        default=None, description="Only requests this enrolled device submitted."
    )
    unfinished: bool = Field(
        default=False,
        description="Only requests whose goal has not yet been seen finished (no finished_at).",
    )
    received_since: UtcDatetime | None = Field(
        default=None,
        description="Only requests first committed at or after this time: the Hive Entrance "
        "reads a device's goals of the last day to weigh its daily spend cap.",
    )
    limit: int = Field(
        default=DEFAULT_REQUEST_PAGE,
        ge=1,
        le=MAX_REQUEST_PAGE,
        description="Most requests to return; the store truncates rather than raising.",
    )


def check_goal_request_event(request: GoalRequest, event: QueenEvent) -> None:
    """Require that `event` is one of `request`'s own goal request edges, before any write.

    Args:
        request: The row about to be written.
        event: The event about to be recorded with it.

    Raises:
        InvariantViolationError: `event` is not a `queen.goal_request_*` kind, or its payload
            names a different request.
    """
    # The kind first: the likelier mistake is recording the wrong family of event entirely.
    if not event.kind.startswith(REQUEST_EVENT_PREFIX):
        raise InvariantViolationError(
            f"event {event.id} is {event.kind!r}, not a {REQUEST_EVENT_PREFIX}* edge of goal "
            f"request {request.id}."
        )
    if event.payload.get("goal_request_id") != request.id:
        raise InvariantViolationError(
            f"event {event.id} names goal request {event.payload.get('goal_request_id')!r}, not "
            f"{request.id!r}."
        )


class GoalRequestStore(Protocol):
    """Persist GoalRequests, each write atomic with its trail event; safe to call concurrently."""

    async def insert(self, request: GoalRequest, event: QueenEvent) -> None:
        """Insert a fresh request together with its `queen.goal_request_received` event.

        Args:
            request: The request to store; its id must be new to the store.
            event: The event recording that it arrived.

        Raises:
            GoalRequestExistsError: A request with `request.id` already exists; nothing is
                written.
            InvariantViolationError: `check_goal_request_event` rejects the pair; nothing is
                written.
        """
        ...

    async def update(self, request: GoalRequest, event: QueenEvent) -> None:
        """Replace the stored request with the same id, recording `event` alongside it.

        Args:
            request: The request's new value; its id selects the row to replace.
            event: The event describing this change.

        Raises:
            GoalRequestNotFoundError: No request with `request.id` exists; nothing is written.
            InvariantViolationError: `check_goal_request_event` rejects the pair; nothing is
                written.
        """
        ...

    async def get(self, request_id: str) -> GoalRequest:
        """Return the stored request with id `request_id`.

        Args:
            request_id: The request to look up.

        Returns:
            The matching GoalRequest.

        Raises:
            GoalRequestNotFoundError: No request with that id exists.
        """
        ...

    async def list_requests(self, query: GoalRequestQuery) -> tuple[GoalRequest, ...]:
        """Return every request matching `query`, oldest first by `(received_at, id)`.

        Args:
            query: The filters and limit to apply.

        Returns:
            At most `query.limit` requests satisfying every set field of `query`.
        """
        ...
