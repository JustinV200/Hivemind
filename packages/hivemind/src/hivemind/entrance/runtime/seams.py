"""Implement enrolment's two remaining seams: cutting a device off, and its open goals.

Enrolment leaves two collaborators to the Entrance's composition (ADR-0033). ``DeviceOffboarder``:
a device that leaves APPROVED (locked, revoked, expired) loses, in the same step, every session
(``SessionBook.offboard``), every push subscription and live push socket
(``PushDispatcher.forget_device``) and every WebSocket it holds (the socket registry);
``EntranceOffboarder`` does all three. ``GoalLedger``: a revocation lists the device's open goals
and, when asked, cancels them, having first refused whatever the device asked for that is not
planned yet; ``QueenGoalLedger`` refuses through the Queen (``Queen.refuse_device_requests``: the
Entrance never writes her tables), reads her goal-request table for the device's planned,
unfinished goals, and cancels through her (``Queen.cancel_goal``: placed work is stopped on its
Warden first; work whose Warden cannot be reached is named as left running).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Installed in
    ``EnrolmentSeams`` by the Entrance's composition. Calls into the session book, the push
    dispatcher, the socket registry, the Queen's door and her goal-request table.

Key invariants:
    - Offboarding is idempotent: a device with nothing left to cut changes nothing.
    - Goals are only ever cancelled, and requests refused, through the Queen.

See Also:
    - hivemind.entrance.enrol.deps for the seams' protocols.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.entrance.auth.session import SessionBook
from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.gate import QueenDoor
from hivemind.entrance.push import PushDispatcher
from hivemind.entrance.streams import CloseReason, SocketRegistry
from hivemind.queen import GoalRequestQuery, GoalRequestState, GoalRequestStore
from hivemind.queen.intake import MAX_REQUEST_PAGE
from waggle.ids import DeviceId, TaskId

__all__ = ["EntranceOffboarder", "QueenGoalLedger"]


class EntranceOffboarder:
    """Cut a device that left APPROVED off: sessions, push subscriptions, sockets."""

    def __init__(
        self, sessions: SessionBook, push: PushDispatcher, sockets: SocketRegistry
    ) -> None:
        """Hold the three things a device is cut off from.

        Args:
            sessions: The session book.
            push: The push dispatcher (subscriptions and live push sockets).
            sockets: Every live WebSocket.
        """
        self._sessions = sessions
        self._push = push
        self._sockets = sockets

    async def offboard(self, device_id: DeviceId, reason: DeviceStatus) -> None:
        """End every session, subscription and socket of ``device_id``.

        Args:
            device_id: The device that left APPROVED.
            reason: The status it moved to: LOCKED, REVOKED or EXPIRED.
        """
        # Latency: local table statements, then signals to this process's own sockets.
        await self._sessions.offboard(device_id, reason)
        await self._push.forget_device(device_id)
        self._sockets.close_device(device_id, CloseReason.SESSION_ENDED)


class QueenGoalLedger:
    """A device's requests and open goals, from the Queen's table; refused and cancelled by her."""

    def __init__(self, requests: GoalRequestStore, queen: QueenDoor) -> None:
        """Hold the table and the door.

        Args:
            requests: The Queen's goal-request table (read only).
            queen: The Queen's door, the only way a goal is cancelled.
        """
        self._requests = requests
        self._queen = queen

    async def refuse_requests(self, device_id: DeviceId, reason: str) -> tuple[str, ...]:
        """Have the Queen refuse every request ``device_id`` submitted that is not planned yet.

        Args:
            device_id: The device being revoked.
            reason: For the human, kept on each refused request.

        Returns:
            The goal requests refused.
        """
        # Latency: local goal-request edges in the Queen's own tables, in this process.
        return await self._queen.refuse_device_requests(device_id, reason)

    async def open_goals(self, device_id: DeviceId) -> tuple[TaskId, ...]:
        """Return the planned goals ``device_id`` submitted that have not finished, oldest first.

        Args:
            device_id: The device being revoked.

        Returns:
            Each open goal's id.
        """
        query = GoalRequestQuery(
            device_id=device_id,
            state=GoalRequestState.PLANNED,
            unfinished=True,
            limit=MAX_REQUEST_PAGE,
        )
        # Latency: one local indexed read of the Queen's goal-request table.
        requests = await self._requests.list_requests(query)
        return tuple(request.goal_id for request in requests if request.goal_id is not None)

    async def cancel_goals(self, goal_ids: Sequence[TaskId], reason: str) -> tuple[TaskId, ...]:
        """Cancel each goal through the Queen; return those now fully ended.

        Args:
            goal_ids: The goals to cancel.
            reason: What each ``task.cancelled`` records.

        Returns:
            The goals with no task left unfinished.
        """
        cancelled: list[TaskId] = []
        # One goal at a time: each is a few local chamber transactions.
        for goal_id in goal_ids:
            if await self._queen.cancel_goal(goal_id, reason):
                cancelled.append(goal_id)
        return tuple(cancelled)
