"""Define _LifecycleMixin: BroodChamber's placement, progress and Clustering transitions.

Placement (`assign`, `unassign`, `start`) moves a task between PENDING, ASSIGNED and RUNNING as a
Warden (a Cell's supervisor) takes on and starts the task; `report_progress` records a running
task's latest summary with no status change; `pause`/`resume` move a task in and out of PAUSED for
Clustering (the Queen suspending work while a model provider is unavailable). Every transition
here (other than `report_progress`, which is not one) is asserted legal by
`hivemind.brood_chamber.task_state.TRANSITIONS` before it is written, through
`_ChamberBase._transition` (`hivemind.brood_chamber.chamber.base`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `BroodChamber`
    (`hivemind.brood_chamber.chamber`); not imported anywhere else. Calls into
    `hivemind.brood_chamber.chamber.base`, `hivemind.brood_chamber.task_state` and
    `hivemind.brood_chamber.errors` only.

Key invariants:
    - `assign` never changes `attempt`; `unassign` is the one place `attempt` advances, so a
      later `assign` on the same task already reflects how many times it has been placed, even
      across a process restart (the count lives on the stored Task, never in memory).
    - `report_progress` requires the task to already be RUNNING, checked directly rather than
      through `assert_transition` (which would wrongly allow it from ASSIGNED, since ASSIGNED can
      legally *reach* RUNNING without *being* RUNNING yet).

See Also:
    - hivemind.brood_chamber.chamber.base for _ChamberBase, the shared write helpers this mixin
      uses.
    - hivemind.brood_chamber.task_state for the transition table every move here is checked
      against.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.task import Task
from hivemind.brood_chamber.task_state import TaskStatus
from waggle.ids import CellId, TaskId, WardenId

__all__: list[str] = []  # Private mixin: nothing here is part of the package's public API.


class _LifecycleMixin(_ChamberBase):
    """BroodChamber's placement, progress-reporting and Clustering methods."""

    async def assign(
        self, task_id: TaskId, warden_id: WardenId, cell_id: CellId, reason: str
    ) -> Task:
        """Move a task PENDING -> ASSIGNED, placing it on `warden_id`'s `cell_id`.

        `attempt` is left unchanged here: it only advances when a task returns to PENDING via
        `unassign`, so the value this assign writes already reflects how many times the task has
        been placed.

        Args:
            task_id: The task to place.
            warden_id: The Warden now supervising this task's Cell.
            cell_id: The Cell the task will run on.
            reason: Why this placement was chosen, for the trail.

        Returns:
            The task, now ASSIGNED.
        """
        task = await self._store.get_task(task_id)
        payload: dict[str, JsonValue] = {
            "warden_id": warden_id,
            "cell_id": cell_id,
            "attempt": task.attempt,
            "reason": reason,
        }
        return await self._transition(
            task,
            TaskStatus.ASSIGNED,
            "task.assigned",
            payload,
            warden_id=warden_id,
            cell_id=cell_id,
        )

    async def unassign(self, task_id: TaskId, reason: str) -> Task:
        """Move a task ASSIGNED -> PENDING: its Warden was lost before the Worker started.

        Bumps `attempt` by one, so the next `assign` on this task already reflects a new attempt.

        Args:
            task_id: The task to unassign.
            reason: Why the placement was lost, for the trail.

        Returns:
            The task, now PENDING with no placement and an incremented `attempt`.
        """
        task = await self._store.get_task(task_id)
        payload: dict[str, JsonValue] = {"reason": reason}
        return await self._transition(
            task,
            TaskStatus.PENDING,
            "task.unassigned",
            payload,
            warden_id=None,
            cell_id=None,
            attempt=task.attempt + 1,
        )

    async def start(self, task_id: TaskId) -> Task:
        """Move a task ASSIGNED -> RUNNING: its Warden's Worker has started.

        Args:
            task_id: The task that started.

        Returns:
            The task, now RUNNING.
        """
        task = await self._store.get_task(task_id)
        return await self._transition(task, TaskStatus.RUNNING, "task.started", {})

    async def report_progress(
        self, task_id: TaskId, summary: str, fraction_done: float | None = None
    ) -> Task:
        """Record a progress report on a RUNNING task; no status change.

        Args:
            task_id: The task reporting progress.
            summary: The latest human-readable summary, stored on `Task.last_summary`. Never
                copied into the trail event; only its length is (codingrules section 12).
            fraction_done: The Worker's own progress estimate, 0 to 1, when it has one.

        Returns:
            The task, still RUNNING, with `last_summary` and `fraction_done` updated.

        Raises:
            InvalidTransitionError: The task is not RUNNING; progress only makes sense while a
                Worker is actively on it.
        """
        task = await self._store.get_task(task_id)
        if task.status is not TaskStatus.RUNNING:
            raise InvalidTransitionError(task.status, TaskStatus.RUNNING, subject_id=task.id)
        payload: dict[str, JsonValue] = {
            "fraction_done": fraction_done,
            "summary_length": len(summary),
        }
        return await self._write(
            task, "task.progressed", payload, last_summary=summary, fraction_done=fraction_done
        )

    async def pause(self, task_id: TaskId, reason: str) -> Task:
        """Move a task RUNNING -> PAUSED: Clustering suspended it.

        Args:
            task_id: The task to pause.
            reason: Why it was paused (typically which provider set became unavailable).

        Returns:
            The task, now PAUSED.
        """
        task = await self._store.get_task(task_id)
        payload: dict[str, JsonValue] = {"reason": reason}
        return await self._transition(task, TaskStatus.PAUSED, "task.paused", payload)

    async def resume(self, task_id: TaskId, reason: str) -> Task:
        """Move a task PAUSED -> RUNNING: Clustering resumed it.

        Args:
            task_id: The task to resume.
            reason: Why it resumed now, for the trail.

        Returns:
            The task, now RUNNING.
        """
        task = await self._store.get_task(task_id)
        payload: dict[str, JsonValue] = {"reason": reason}
        return await self._transition(task, TaskStatus.RUNNING, "task.resumed", payload)
