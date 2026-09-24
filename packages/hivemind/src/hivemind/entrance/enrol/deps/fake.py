"""Provide recording fakes of the enrolment seams: a notifier, an offboarder, a goal ledger.

Codingrules 14.4 keeps fakes beside their protocols, honest and production quality. Tests (and
demos) need to see what enrolment asked of its seams: which notices it sent, which devices it cut
off, which goals it cancelled. ``RecordingSecurityNotifier`` and ``RecordingDeviceOffboarder``
keep every call in order; ``FakeGoalLedger`` holds each device's unplanned requests and open
goals, refuses and cancels them on request, and can be told that some goals finish before they can
be cancelled, so a revocation's "goals left running" can be exercised honestly.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.deps``. Used by
    the enrolment tests and builders; never by a production composition root. Calls into
    ``hivemind.entrance.enrol.deps`` (the seams' shapes), ``hivemind.entrance.enrol.state`` and
    waggle (ids) only.

Key invariants:
    - Each fake satisfies its protocol exactly (``SecurityNotifier``, ``DeviceOffboarder``,
      ``GoalLedger``) and records calls in the order they were made.
    - ``FakeGoalLedger.cancel_goals`` returns only goals that were open and cancellable, and
      removes them from the device's open goals.

See Also:
    - hivemind.entrance.enrol.deps.notifier, .offboarder and .goals for the protocols faked here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hivemind.entrance.enrol.deps.notifier import SecurityNotice
from hivemind.entrance.enrol.state import DeviceStatus
from waggle.ids import DeviceId, TaskId

__all__ = ["FakeGoalLedger", "RecordingDeviceOffboarder", "RecordingSecurityNotifier"]


class RecordingSecurityNotifier:
    """A SecurityNotifier that keeps every notice it is given, in order."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[SecurityNotice] = []

    async def notify(self, notice: SecurityNotice) -> None:
        """Keep ``notice``.

        Args:
            notice: The notice enrolment sent.
        """
        self.notices.append(notice)


class RecordingDeviceOffboarder:
    """A DeviceOffboarder that keeps every device it is asked to cut off, with the reason."""

    def __init__(self) -> None:
        """Start with no devices offboarded."""
        self.offboarded: list[tuple[DeviceId, DeviceStatus]] = []

    async def offboard(self, device_id: DeviceId, reason: DeviceStatus) -> None:
        """Keep ``(device_id, reason)``.

        Args:
            device_id: The device that left APPROVED.
            reason: The status it moved to.
        """
        self.offboarded.append((device_id, reason))


class FakeGoalLedger:
    """A GoalLedger over in-memory open goals per device, recording every cancellation."""

    def __init__(
        self,
        open_goals: Mapping[DeviceId, Sequence[TaskId]] | None = None,
        finishing: frozenset[TaskId] = frozenset(),
    ) -> None:
        """Start with ``open_goals`` per device.

        Args:
            open_goals: Each device's open goals, oldest first; None for none anywhere.
            finishing: Goals that finish before a cancellation reaches them: they stay open for
                ``open_goals`` but ``cancel_goals`` never cancels them.
        """
        self._open: dict[DeviceId, list[TaskId]] = {
            device_id: list(goals) for device_id, goals in (open_goals or {}).items()
        }
        self._finishing = finishing
        self._unplanned: dict[DeviceId, list[str]] = {}
        self.cancellations: list[tuple[tuple[TaskId, ...], str]] = []
        self.refusals: list[tuple[DeviceId, str]] = []

    def request(self, device_id: DeviceId, request_ids: Sequence[str]) -> None:
        """Record that ``device_id`` has goal requests not planned yet; a test's arrange step.

        Args:
            device_id: The submitting device.
            request_ids: Its unplanned requests, oldest first.
        """
        self._unplanned.setdefault(device_id, []).extend(request_ids)

    async def refuse_requests(self, device_id: DeviceId, reason: str) -> tuple[str, ...]:
        """Refuse the device's unplanned requests; see GoalLedger.refuse_requests."""
        self.refusals.append((device_id, reason))
        return tuple(self._unplanned.pop(device_id, ()))

    def submit(self, device_id: DeviceId, goal_ids: Sequence[TaskId]) -> None:
        """Record that ``device_id`` submitted ``goal_ids``, still open; a test's arrange step.

        Args:
            device_id: The submitting device, known once it has enrolled.
            goal_ids: Its new goals, oldest first.
        """
        self._open.setdefault(device_id, []).extend(goal_ids)

    async def open_goals(self, device_id: DeviceId) -> tuple[TaskId, ...]:
        """Return the device's open goals; see GoalLedger.open_goals."""
        return tuple(self._open.get(device_id, ()))

    async def cancel_goals(self, goal_ids: Sequence[TaskId], reason: str) -> tuple[TaskId, ...]:
        """Cancel every open, cancellable goal named; see GoalLedger.cancel_goals."""
        still_open = {goal for goals in self._open.values() for goal in goals}
        cancelled = tuple(
            goal for goal in goal_ids if goal in still_open and goal not in self._finishing
        )
        # Cancelled goals are no longer open for any device.
        for goals in self._open.values():
            goals[:] = [goal for goal in goals if goal not in cancelled]
        self.cancellations.append((cancelled, reason))
        return cancelled
