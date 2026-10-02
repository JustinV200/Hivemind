"""Define _LifecycleMixin: BroodChamber's placement, progress and Clustering transitions.

Placement (`assign`, `unassign`, `start`) moves a task between PENDING, ASSIGNED and RUNNING as a
Warden (a Cell's supervisor) takes on and starts the task; since roadmap step 10.3b `assign` also
binds the task to its Cell's Comb Shield tier (`Task.bound_tier`, recorded on `task.assigned`)
and `unassign` unbinds it, so a task moved to another Cell is re-bound before it runs again.
`requeue_for_commands` (ADR-0060) moves a RUNNING task back to PENDING the same way, for a task
whose Cell refused the commands its Worker or its acceptance runs: its needs gain `commands`, so
its next placement lands on a Cell that runs them.
`report_progress` records a running task's latest summary with no status change; `pause`/`resume`
move a task in and out of PAUSED for Clustering (the Queen suspending work while a model provider
is unavailable). Every transition
here (other than `report_progress`, which is not one) is asserted legal by
`hivemind.brood_chamber.task.state.TRANSITIONS` before it is written, through
`_ChamberBase._transition` (`hivemind.brood_chamber.chamber.base`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `BroodChamber`
    (`hivemind.brood_chamber.chamber`); not imported anywhere else. Calls into
    `hivemind.brood_chamber.chamber.base`, `hivemind.brood_chamber.task.state`,
    `hivemind.brood_chamber.errors`, `hivemind.cell` (the tier enum) and `hivemind.common.errors`
    (ConflictError) only.

Key invariants:
    - `assign` never changes `attempt`; `unassign` and `requeue_for_commands` are the only places
      `attempt` advances, so a
      later `assign` on the same task already reflects how many times it has been placed, even
      across a process restart (the count lives on the stored Task, never in memory).
    - `requeue_for_commands` changes the spec's `needs.commands` and nothing else in it, and only
      ever from false to true, so a task is re-placed for this reason at most once.
    - `assign` is the only write that binds a tier and `unassign` (with every terminal
      transition, `hivemind.brood_chamber.chamber.outcomes`) the only ones that clear it, so a
      placed task's binding always names the Cell it was last assigned to.
    - `report_progress` requires the task to already be RUNNING, checked directly rather than
      through `assert_transition` (which would wrongly allow it from ASSIGNED, since ASSIGNED can
      legally *reach* RUNNING without *being* RUNNING yet).

See Also:
    - hivemind.brood_chamber.chamber.base for _ChamberBase, the shared write helpers this mixin
      uses.
    - hivemind.brood_chamber.task.state for the transition table every move here is checked
      against.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.cell import CombShieldLevel
from hivemind.common.errors import ConflictError
from waggle.ids import CellId, TaskId, WardenId

__all__: list[str] = []  # Private mixin: nothing here is part of the package's public API.


class _LifecycleMixin(_ChamberBase):
    """BroodChamber's placement, progress-reporting and Clustering methods."""

    async def assign(
        self,
        task_id: TaskId,
        warden_id: WardenId,
        cell_id: CellId,
        reason: str,
        *,
        bound_tier: CombShieldLevel,
    ) -> Task:
        """Move a task PENDING -> ASSIGNED, placing it on `warden_id`'s `cell_id` at its tier.

        `attempt` is left unchanged here: it only advances when a task returns to PENDING via
        `unassign`, so the value this assign writes already reflects how many times the task has
        been placed.

        Args:
            task_id: The task to place.
            warden_id: The Warden now supervising this task's Cell.
            cell_id: The Cell the task will run on.
            reason: Why this placement was chosen, for the trail.
            bound_tier: The Cell's Comb Shield tier, which the task is bound to from now until it
                leaves the Cell (roadmap step 10.3b, codingrules 8.7: a task inherits the tier of
                the Cell where it executes).

        Returns:
            The task, now ASSIGNED and bound to `bound_tier`.
        """
        task = await self._store.get_task(task_id)
        payload: dict[str, JsonValue] = {
            "warden_id": warden_id,
            "cell_id": cell_id,
            "attempt": task.attempt,
            "bound_tier": bound_tier.value,
            "reason": reason,
        }
        return await self._transition(
            task,
            TaskStatus.ASSIGNED,
            "task.assigned",
            payload,
            warden_id=warden_id,
            cell_id=cell_id,
            bound_tier=bound_tier,
        )

    async def unassign(self, task_id: TaskId, reason: str) -> Task:
        """Move a task ASSIGNED or PAUSED -> PENDING: its placement is gone and it is placed afresh.

        ASSIGNED: its Warden was lost before the Worker started. PAUSED: its Cell's device was
        lost for good and the Queen took the task back (ADR-0053); a running task is paused first.
        RUNNING's own edge back to PENDING is `requeue_for_commands`'s alone (ADR-0060), so it is
        refused here.

        Bumps `attempt` by one, so the next `assign` on this task already reflects a new attempt.
        The Queen's dispatch pass calls this for a task it finds stranded ASSIGNED (a stop or a
        failure between `assign` and `start`; `hivemind.queen.dispatcher.ready.dispatch`).

        Args:
            task_id: The task to unassign.
            reason: Why the placement was lost, for the trail.

        Returns:
            The task, now PENDING with no placement, no tier binding (its next placement re-binds
            it to whichever Cell it lands on) and an incremented `attempt`.
        """
        task = await self._store.get_task(task_id)
        if task.status is TaskStatus.RUNNING:
            # RUNNING -> PENDING is for re-placement only; a plain give-back pauses first.
            raise InvalidTransitionError(task.status, TaskStatus.PENDING, subject_id=task.id)
        payload: dict[str, JsonValue] = {"reason": reason}
        return await self._transition(
            task,
            TaskStatus.PENDING,
            "task.unassigned",
            payload,
            warden_id=None,
            cell_id=None,
            bound_tier=None,
            attempt=task.attempt + 1,
        )

    async def requeue_for_commands(self, task_id: TaskId, reason: str, attempt: int) -> Task:
        """Move a RUNNING task back to PENDING, needing a Cell that runs commands (ADR-0060).

        For a task whose Cell refused the commands it runs: a retry there would be refused again,
        so it is placed afresh with `needs.commands` set, which placement rule 4d only lets land
        on a Cell that runs them.

        Args:
            task_id: The RUNNING task to re-place.
            reason: Why its placement was given up, for the trail.
            attempt: The attempt its next placement carries; the stored attempt never goes
                backwards, so the larger of this and one past the stored one is written.

        Returns:
            The task, now PENDING with no placement or tier binding, `needs.commands` set and its
            attempt advanced.

        Raises:
            InvalidTransitionError: The task is not RUNNING.
            ConflictError: The task already needs commands (re-placed for them once already, or
                planned so), so there is no wider placement to give it.
        """
        task = await self._store.get_task(task_id)
        if task.spec.needs.commands:
            # Once is the bound: a task that already needs commands has nowhere wider to go.
            raise ConflictError(
                f"Task {task_id} already needs commands; it is re-placed for them at most once."
            )
        needs = task.spec.needs.model_copy(update={"commands": True})
        spec = task.spec.model_copy(update={"needs": needs})
        payload: dict[str, JsonValue] = {"reason": reason, "from_status": task.status.value}
        return await self._transition(
            task,
            TaskStatus.PENDING,
            "task.unassigned",
            payload,
            spec=spec,
            warden_id=None,
            cell_id=None,
            bound_tier=None,
            attempt=max(attempt, task.attempt + 1),
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
        """Move a task RUNNING -> PAUSED: Clustering suspended it, or its bee was quarantined.

        Args:
            task_id: The task to pause.
            reason: Why it was paused (which provider set became unavailable, or which bee's
                quarantine holds it).

        Returns:
            The task, now PAUSED.
        """
        task = await self._store.get_task(task_id)
        payload: dict[str, JsonValue] = {"reason": reason}
        return await self._transition(task, TaskStatus.PAUSED, "task.paused", payload)

    async def resume(self, task_id: TaskId, reason: str) -> Task:
        """Move a task PAUSED -> RUNNING: Clustering resumed it, or its quarantine was lifted.

        Args:
            task_id: The task to resume.
            reason: Why it resumed now, for the trail.

        Returns:
            The task, now RUNNING.
        """
        task = await self._store.get_task(task_id)
        payload: dict[str, JsonValue] = {"reason": reason}
        return await self._transition(task, TaskStatus.RUNNING, "task.resumed", payload)
