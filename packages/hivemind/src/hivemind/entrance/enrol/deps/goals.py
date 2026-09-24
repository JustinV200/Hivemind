"""Define GoalLedger: the seam through which a revocation sees, and may cancel, a device's goals.

Roadmap 10.5d: revoking a device lists the goals it submitted that are still open and, when the
operator asks (``--cancel-goals``), cancels them in the same step; the revocation event then names
the goals left running. Which goal came from which device is the Queen's goal-request table, a
later step; a goal's id is its first task's id (``hivemind.brood_chamber``). ``GoalLedger`` is the
seam: enrolment asks it for a device's open goals and to cancel some of them, and that later step
implements it. ``NullGoalLedger`` is the documented no-op until then, which is honest: no device
can submit a goal before the goal routes exist, so every device has none open.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.deps``. Called by
    ``hivemind.entrance.enrol.standing.revoke``; implemented by the Queen's goal-request table
    (a later step) and by ``hivemind.entrance.enrol.deps.fake`` for tests. Calls into waggle (ids)
    only.

Key invariants:
    - ``cancel_goals`` returns the goals it actually cancelled; a goal that finished meanwhile
      is simply not among them, so the revocation event can name exactly what is left running.

See Also:
    - .claude/roadmap.md step 10.5d for revocation's goal handling.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from waggle.ids import DeviceId, TaskId

__all__ = ["GoalLedger", "NullGoalLedger"]


class GoalLedger(Protocol):
    """List a device's open goals and cancel goals on request."""

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
    """The no-op GoalLedger: no device has goals until the goal routes exist, so none are open.

    The Queen's goal-request table (a later step) replaces this in the composition root.
    """

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
