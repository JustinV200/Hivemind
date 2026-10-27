"""Define _NurseMixin: BroodChamber's count of the Nurse Bee rounds a task was sent back for.

ADR-0061: when a Warden's Nurse Bee rejects an attempt, the Queen sends the task back to fix
forward on the same Cell until `[nurse] max_rounds` is spent. `note_nurse_round` is the one write
that counts such a round, on the stored Task (`Task.nurse_rounds`), so the Queen's cap holds
across her restart. It is no transition: the task stays RUNNING, as `report_progress` leaves it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber.chamber`.
    Mixed into `BroodChamber`; called by `hivemind.queen.ticks.nurse.send_back`. Calls into
    `hivemind.brood_chamber.chamber.base`, `hivemind.brood_chamber.errors`,
    `hivemind.brood_chamber.task` and waggle only.

Key invariants:
    - `note_nurse_round` only ever adds one, and only to a RUNNING task.
    - Its `task.nurse_round` event carries the new count alone, never a finding's words.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.queen.autopilot.table for the rule that reads the count against the cap.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from waggle.ids import TaskId

__all__: list[str] = []  # Private mixin: nothing here is part of the package's public API.


class _NurseMixin(_ChamberBase):
    """BroodChamber's Nurse Bee round count (ADR-0061)."""

    async def note_nurse_round(self, task_id: TaskId) -> Task:
        """Count one more Nurse Bee rejection on a RUNNING task the Queen sends back.

        Args:
            task_id: The task whose attempt the Nurse rejected.

        Returns:
            The task, still RUNNING, with `nurse_rounds` one higher.

        Raises:
            InvalidTransitionError: The task is not RUNNING; only a running task is sent back.
        """
        task = await self._store.get_task(task_id)
        if task.status is not TaskStatus.RUNNING:
            raise InvalidTransitionError(task.status, TaskStatus.RUNNING, subject_id=task.id)
        rounds = task.nurse_rounds + 1
        payload: dict[str, JsonValue] = {"nurse_rounds": rounds}
        return await self._write(task, "task.nurse_round", payload, nurse_rounds=rounds)
