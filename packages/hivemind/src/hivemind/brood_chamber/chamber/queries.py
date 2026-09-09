"""Define _QueriesMixin: BroodChamber's four read-only methods.

`get`, `list`, `next_ready` and `pending_questions` never write anything: each is a thin
pass-through to `TaskStore` (`hivemind.brood_chamber.store`), except `next_ready`, which
additionally runs the pure `hivemind.brood_chamber.graph.ready_tasks` decision over the tasks a
store read returns. Reads need no real `ChamberIdentity` (docs/PHASE2_BRIEF.md decision 3), since
nothing here builds a `TaskEvent`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `BroodChamber`
    (`hivemind.brood_chamber.chamber`); not imported anywhere else. Calls into
    `hivemind.brood_chamber.chamber.base` and `hivemind.brood_chamber.graph` only.

Key invariants:
    - None of these methods calls `_ChamberBase._write` or `_transition`: every one is read-only.

See Also:
    - hivemind.brood_chamber.graph for ready_tasks, the pure function `next_ready` wraps.
    - hivemind.brood_chamber.store for TaskFilter and TaskStore, the protocol every method here
      reads through.
"""

from __future__ import annotations

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.graph import ready_tasks
from hivemind.brood_chamber.questions import Question, QuestionStatus
from hivemind.brood_chamber.store import TaskFilter
from hivemind.brood_chamber.task import Task
from waggle.ids import TaskId

__all__: list[str] = []  # Private mixin: nothing here is part of the package's public API.


class _QueriesMixin(_ChamberBase):
    """BroodChamber's four read-only methods: get, list, next_ready and pending_questions."""

    async def next_ready(self, goal_id: TaskId | None = None) -> Task | None:
        """Return the earliest PENDING task whose dependencies have all SUCCEEDED.

        Args:
            goal_id: Restrict the search to tasks under this goal, or search every task.

        Returns:
            The first ready task by `(created_at, id)`, or None when none is ready.
        """
        tasks = await self._store.list_tasks(TaskFilter(goal_id=goal_id))
        ready = ready_tasks(tasks)
        return ready[0] if ready else None

    async def get(self, task_id: TaskId) -> Task:
        """Return one task by id.

        Args:
            task_id: The task to look up.

        Returns:
            The matching Task.
        """
        return await self._store.get_task(task_id)

    async def list(self, query: TaskFilter) -> tuple[Task, ...]:
        """Return every task matching `query`, ordered by `(created_at, id)`.

        Args:
            query: The filters and limit to apply.

        Returns:
            The matching tasks.
        """
        return await self._store.list_tasks(query)

    async def pending_questions(self, task_id: TaskId | None = None) -> tuple[Question, ...]:
        """Return every ASKED question, optionally restricted to one task.

        Args:
            task_id: Restrict to this task's questions, or return every pending question.

        Returns:
            The matching questions, ordered by `(asked_at, id)`.
        """
        return await self._store.list_questions(task_id, QuestionStatus.ASKED)
