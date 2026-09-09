"""Define _OutcomesMixin: BroodChamber's terminal-status methods, complete, fail and cancel.

A task's story ends once it reaches SUCCEEDED, FAILED or CANCELLED
(`hivemind.brood_chamber.task.state.TERMINAL_STATUSES`); this module holds the three ways it gets
there. `complete`/`fail` each require the caller's `TaskOutcome`
(`hivemind.brood_chamber.task.model.TaskOutcome`) to already carry the matching terminal status,
since the Warden that ran acceptance checks is the one that decided it, not this chamber; `cancel`
is the odd one out, building its own `TaskOutcome` from a plain `reason` string because cancelling
carries no separate verification step.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `BroodChamber`
    (`hivemind.brood_chamber.chamber`); not imported anywhere else. Calls into
    `hivemind.brood_chamber.chamber.base`, `hivemind.brood_chamber.task.state` and
    `hivemind.common.errors` only.

Key invariants:
    - `complete`/`fail` reject a mismatched `outcome.status` before touching the store, so a
      caller's bug never reaches `_transition` and fails against the wrong edge of
      `hivemind.brood_chamber.task.state.TRANSITIONS` instead of the real problem.
    - Every terminal transition here clears `warden_id`/`cell_id` (a terminal Task is never
      "placed", per `Task`'s own invariants); `cancel` also clears `pending_question_id`, since
      `BLOCKED -> CANCELLED` is a legal edge and a cancelled task can never still be blocked.

See Also:
    - hivemind.brood_chamber.task.model for TaskOutcome, the value every method here records.
    - hivemind.brood_chamber.chamber.base for _ChamberBase, the shared write helpers this mixin
      uses.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.task.model import Task, TaskOutcome
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.common.errors import InvariantViolationError
from waggle.ids import TaskId

__all__: list[str] = []  # Private mixin: nothing here is part of the package's public API.


class _OutcomesMixin(_ChamberBase):
    """BroodChamber's three terminal-status methods: complete, fail and cancel."""

    async def complete(self, task_id: TaskId, outcome: TaskOutcome) -> Task:
        """Move a task RUNNING -> SUCCEEDED, recording `outcome`.

        Args:
            task_id: The task that succeeded.
            outcome: How it ended; `outcome.status` must already be SUCCEEDED.

        Returns:
            The task, now SUCCEEDED.

        Raises:
            InvariantViolationError: `outcome.status` is not SUCCEEDED.
        """
        payload: dict[str, JsonValue] = {
            "verified_by": outcome.verified_by,
            "spend_usd": outcome.spend_usd,
        }
        return await self._finish(task_id, outcome, TaskStatus.SUCCEEDED, "task.succeeded", payload)

    async def fail(self, task_id: TaskId, outcome: TaskOutcome) -> Task:
        """Move a task RUNNING -> FAILED, recording `outcome`.

        Args:
            task_id: The task that failed.
            outcome: How it ended; `outcome.status` must already be FAILED.

        Returns:
            The task, now FAILED.

        Raises:
            InvariantViolationError: `outcome.status` is not FAILED.
        """
        payload: dict[str, JsonValue] = {"spend_usd": outcome.spend_usd}
        return await self._finish(task_id, outcome, TaskStatus.FAILED, "task.failed", payload)

    async def cancel(self, task_id: TaskId, reason: str) -> Task:
        """Move any non-terminal task -> CANCELLED, building its outcome from `reason`.

        Args:
            task_id: The task to cancel.
            reason: Why it was cancelled; becomes the outcome's summary and the trail's reason.

        Returns:
            The task, now CANCELLED.
        """
        task = await self._store.get_task(task_id)
        outcome = TaskOutcome(status=TaskStatus.CANCELLED, summary=reason)
        payload: dict[str, JsonValue] = {"reason": reason, "from_status": task.status.value}
        return await self._transition(
            task,
            TaskStatus.CANCELLED,
            "task.cancelled",
            payload,
            outcome=outcome,
            warden_id=None,
            cell_id=None,
            pending_question_id=None,
        )

    async def _finish(
        self,
        task_id: TaskId,
        outcome: TaskOutcome,
        expected_status: TaskStatus,
        kind: str,
        payload: Mapping[str, JsonValue],
    ) -> Task:
        """Require `outcome.status` to match, transition to it, and clear placement fields."""
        if outcome.status is not expected_status:
            raise InvariantViolationError(
                f"cannot record task {task_id!r} outcome: outcome.status is "
                f"{outcome.status.name}, expected {expected_status.name}."
            )
        task = await self._store.get_task(task_id)
        return await self._transition(
            task, expected_status, kind, payload, outcome=outcome, warden_id=None, cell_id=None
        )
