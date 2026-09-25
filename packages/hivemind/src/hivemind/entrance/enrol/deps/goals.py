"""Define GoalLedger: the seam through which a revocation sees, and may cancel, a device's goals.

Roadmap 10.5d: revoking a device lists the goals it submitted that are still open and, when the
operator asks (``--cancel-goals``), cancels them in the same step; the revocation event then names
the goals left running. Whatever the device asked for that is not planned yet is refused in the
same step, whatever the operator asked, so a revoked device's work never starts. Which goal came
from which device is the Queen's goal-request table; a goal's id is its first task's id
(``hivemind.brood_chamber``). ``GoalLedger`` is the seam: enrolment asks it to refuse a device's
unplanned requests, for its open goals and to cancel some of them, and the Entrance runtime
implements it over the Queen (``hivemind.entrance.runtime.seams.QueenGoalLedger``).
``NullGoalLedger`` is the no-op for an enrolment with no Queen behind it (tests, tools).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.deps``. Called by
    ``hivemind.entrance.enrol.standing.revoke``; implemented over the Queen by the Entrance
    runtime and by ``hivemind.entrance.enrol.deps.fake`` for tests. Calls into waggle (ids) only.

Key invariants:
    - ``cancel_goals`` returns the goals it actually cancelled; a goal that finished meanwhile
      is simply not among them, so the revocation event can name exactly what is left running.
    - ``refuse_requests`` refuses only requests not yet planned; one planned meanwhile is an open
      goal, for ``open_goals`` and ``cancel_goals``.

See Also:
    - .claude/roadmap.md step 10.5d for revocation's goal handling.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from waggle.ids import DeviceId, TaskId

__all__ = ["GoalLedger", "NullGoalLedger"]


class GoalLedger(Protocol):
    """Refuse a device's unplanned requests, list its open goals, and cancel goals on request."""

    async def refuse_requests(self, device_id: DeviceId, reason: str) -> tuple[str, ...]:
        """Refuse every goal request ``device_id`` submitted that has not been planned yet.

        Args:
            device_id: The device being revoked.
            reason: For the human, kept on each refused request (never on the trail).

        Returns:
            The goal requests refused.
        """
        ...

    async def open_goals(self, device_id: DeviceId) -> tuple[TaskId, ...]:
        """Return the goals ``device_id`` submitted that have not finished.

        Args:
            device_id: The device being revoked.

        Returns:
            Each open goal's id (its first task's id), oldest first.
        """
        ...

    async def cancel_goals(self, goal_ids: Sequence[TaskId], reason: str) -> tuple[TaskId, ...]:
        """Cancel ``goal_ids``, returning the ones actually cancelled.

        Args:
            goal_ids: The goals to cancel.
            reason: A short phrase the Queen's trail records with each cancellation.

        Returns:
            The goals now cancelled; one that finished meanwhile is left out.
        """
        ...


class NullGoalLedger:
    """The no-op GoalLedger: with no Queen behind enrolment, no device has requests or goals.

    The Entrance runtime replaces this with ``QueenGoalLedger`` in the composition root.
    """

    async def refuse_requests(self, device_id: DeviceId, reason: str) -> tuple[str, ...]:
        """Refuse nothing; see the class docstring.

        Args:
            device_id: The device, ignored.
            reason: The reason, ignored.

        Returns:
            An empty tuple.
        """
        return ()

    async def open_goals(self, device_id: DeviceId) -> tuple[TaskId, ...]:
        """Return no goals; see the class docstring.

        Args:
            device_id: The device, ignored.

        Returns:
            An empty tuple.
        """
        return ()

    async def cancel_goals(self, goal_ids: Sequence[TaskId], reason: str) -> tuple[TaskId, ...]:
        """Cancel nothing; see the class docstring.

        Args:
            goal_ids: The goals, ignored (``open_goals`` never names any).
            reason: The reason, ignored.

        Returns:
            An empty tuple.
        """
        return ()
