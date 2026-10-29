"""Give the owner their answer to a watch request on the running Queen: list, approve, deny.

ADR-0069: a task that would drive the operator's running screen or audio waits for the owner. The
Hive Entrance shows what waits (`GET /v1/entrance/watch`) and carries the owner's yes or no
(`POST /v1/entrance/watch/{request_id}/approve` and `/deny`), loopback-only rows that need the
Hive Stand's stand key (ADR-0068), through `hivemind.entrance.gate.QueenDoor` to these methods:
the Entrance never writes a Queen book itself. `WatchDoor` is that set of methods as a mixin
`hivemind.queen.queen.Queen` inherits. An answer is recorded on the trail and wakes the Queen, so
her next dispatch pass assigns the approved task or cancels the refused one at once.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    peripherals sub-package. Called by the Hive Entrance's watch routes through `QueenDoor`.
    Calls into `hivemind.queen.trail` and this sub-package's own modules only; `QueenDeps` only
    for its type.

Key invariants:
    - Nothing here decides for the owner: each call is one explicit answer, from one device.
    - The planner, Honey and every bee have no path here; only the Entrance's routes call it.

See Also:
    - hivemind.entrance.routes.entrance.watch for the routes and who may reach them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.queen.peripherals.model import WatchRequest
from hivemind.queen.trail import record_event
from waggle.ids import DeviceId

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps

__all__ = ["WatchDoor"]


class WatchDoor:
    """The owner's answers to watch requests, as methods of the running Queen.

    Reads `self._deps`, set by `Queen.__init__`; this class is never instantiated on its own.
    """

    _deps: QueenDeps

    def pending_watch_requests(self) -> tuple[WatchRequest, ...]:
        """Return every watch request still waiting for the owner, oldest first."""
        return self._deps.dispatch.watch.pending(self._deps.clock.now())

    async def answer_watch_request(
        self, request_id: str, approved: bool, device_id: DeviceId
    ) -> WatchRequest:
        """Settle a watch request on the owner's word, record it, and wake the Queen.

        Args:
            request_id: The request the owner answered.
            approved: Yes or no.
            device_id: The Hive Stand device the owner answered from.

        Returns:
            The settled request.

        Raises:
            UnknownWatchRequestError: No waiting task has that request.
            WatchRequestSettledError: It was already answered, or it expired.
        """
        settled = self._deps.dispatch.watch.decide(
            request_id, approved, device_id, self._deps.clock.now()
        )
        await record_event(
            self._deps,
            "queen.decided",
            settled.task_id,
            reason="owner_approval_answered",
            request_id=settled.id,
            outcome=settled.status.value,
            device_id=device_id,
        )
        # Her next pass assigns it or cancels it now, not on the next idle tick.
        self._deps.wake.set()
        return settled
