"""Define the two ways the owner's answer to a watch request is refused.

ADR-0069: the owner answers a watch request (a task asking for the running screen or audio) from
the Hive Stand. An answer naming no request the Queen holds is `UnknownWatchRequestError` (the
Hive Entrance answers 404); one for a request already answered or expired is
`WatchRequestSettledError` (409), so a second tap or a late one changes nothing and says so.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    peripherals sub-package. Raised by `hivemind.queen.peripherals.book`. Calls into
    `hivemind.common.errors` only.

Key invariants:
    - Each class sets its own stable `code`; messages carry ids and statuses only.

See Also:
    - hivemind.entrance.gate.handlers for the category-to-status mapping.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import ConflictError, NotFoundError

__all__ = ["UnknownWatchRequestError", "WatchRequestSettledError"]


class UnknownWatchRequestError(NotFoundError):
    """Raise when an answer names a watch request this Queen does not hold."""

    code: ClassVar[str] = "hivemind.queen.unknown_watch_request"

    def __init__(self, request_id: str) -> None:
        """Build the error for an unknown request.

        Args:
            request_id: The request the caller named.
        """
        super().__init__(f"No task is waiting for an answer under {request_id}.")
        self.request_id = request_id


class WatchRequestSettledError(ConflictError):
    """Raise when an answer comes for a watch request already answered or expired."""

    code: ClassVar[str] = "hivemind.queen.watch_request_settled"

    def __init__(self, request_id: str, status: str) -> None:
        """Build the error for a request that is no longer waiting.

        Args:
            request_id: The request the caller named.
            status: What it already is (APPROVED, DENIED or EXPIRED).
        """
        super().__init__(f"That request was already settled: it is {status.lower()}.")
        self.request_id = request_id
        self.status = status
