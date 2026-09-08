"""Provide MemoryTaskStore, an in-process TaskStore for tests and demos.

An in-memory task store is two plain Python dicts guarded by a lock: no SQL, no file, gone when
the process exits. It exists so a unit test, a `hive doctor` smoke run, or a demo path can
exercise everything above the store (codingrules 14.4: "fakes live in src/ beside the Protocol")
without a SQLite file. It implements `hivemind.brood_chamber.store.TaskStore` exactly like
`hivemind.brood_chamber.sqlite.SqliteTaskStore` does, which is what the contract suite
(`tests/contracts/test_task_store_contract.py`) proves.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by tests and demos, and by any
    composition root that wants a durable-store-shaped `BroodChamber` (roadmap step 2.8) without a
    database file. Calls into hivemind.brood_chamber (store, task, questions, task_state, errors),
    hivemind.common.errors and hivemind.pheromone (`PheromoneTrail`, `TaskEvent`) only.

Key invariants:
    - Every mutation validates and checks for conflicts first, then awaits `self._trail.record`
      for each event, and only after every record succeeds does it assign the dicts' new values:
      a failed `record` (a `DuplicateEventError`, propagated unchanged) leaves both dicts exactly
      as they were before the call.
    - Every method holds `self._lock` for its whole body, so two coroutines can never interleave a
      read with a write, or two writes with each other.

See Also:
    - hivemind.brood_chamber.store for the TaskStore protocol this class implements, and
      check_task_event, the guard every mutation calls first.
    - hivemind.brood_chamber.sqlite for the durable counterpart.
    - hivemind.pheromone.memory for MemoryPheromoneTrail, the PheromoneTrail this store is built
      on in tests, and the equivalent "validate, record, then swap in" shape it follows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from hivemind.brood_chamber.errors import (
    QuestionNotFoundError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from hivemind.brood_chamber.questions import Question, QuestionStatus
from hivemind.brood_chamber.store import TaskFilter, check_task_event
from hivemind.brood_chamber.task import Task
from hivemind.common.errors import ConflictError, InvariantViolationError
from hivemind.pheromone import PheromoneTrail, TaskEvent
from waggle.ids import MessageId, TaskId

__all__ = ["MemoryTaskStore"]


class MemoryTaskStore:
    """An in-process TaskStore: two dicts (tasks by id, questions by id), guarded by one lock."""

    def __init__(self, trail: PheromoneTrail) -> None:
        """Create an empty store over `trail`.

        Args:
            trail: Where every mutation's event(s) are recorded before the dicts change.
        """
        self._trail = trail
        self._tasks: dict[TaskId, Task] = {}
        self._questions: dict[MessageId, Question] = {}
        # Guards both dicts together: every public method holds this for its whole body, so a
        # reader never observes a task written without its question (insert_question) or vice
        # versa, and two writers can never race on the same id.
        self._lock = asyncio.Lock()

    async def insert_tasks(self, tasks: Sequence[Task], events: Sequence[TaskEvent]) -> None:
        """Insert every task in `tasks`, each with its own event; see TaskStore.insert_tasks."""
        if len(tasks) != len(events):
            raise InvariantViolationError(
                f"insert_tasks got {len(tasks)} tasks but {len(events)} events; they must match."
            )
        for task, event in zip(tasks, events, strict=True):
            check_task_event(task, event)

        async with self._lock:
            # Any id already present makes the whole call fail before anything is recorded.
            existing = [task.id for task in tasks if task.id in self._tasks]
            if existing:
                raise TaskAlreadyExistsError(existing[0])
            for event in events:
                await self._trail.record(event)
            for task in tasks:
                self._tasks[task.id] = task

    async def update_task(self, task: Task, event: TaskEvent) -> None:
        """Replace the stored task and record `event`; see TaskStore.update_task."""
        check_task_event(task, event)
        async with self._lock:
            if task.id not in self._tasks:
                raise TaskNotFoundError(task.id)
            await self._trail.record(event)
            self._tasks[task.id] = task

    async def get_task(self, task_id: TaskId) -> Task:
        """Return the stored task with id `task_id`; see TaskStore.get_task."""
        async with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    async def list_tasks(self, query: TaskFilter) -> tuple[Task, ...]:
        """Return every task matching `query`, ordered by (created_at, id); see TaskStore."""
        async with self._lock:
            # Copy while holding the lock; filtering and sorting below never touch shared state.
            tasks = list(self._tasks.values())
        matches = [task for task in tasks if _matches_task_filter(task, query)]
        matches.sort(key=lambda task: (task.created_at, task.id))
        return tuple(matches[: query.limit])

    async def insert_question(self, task: Task, question: Question, event: TaskEvent) -> None:
        """Write `task`, `question` and `event` together; see TaskStore.insert_question."""
        check_task_event(task, event)
        async with self._lock:
            if task.id not in self._tasks:
                raise TaskNotFoundError(task.id)
            if question.id in self._questions:
                raise ConflictError(
                    f"A question with id {question.id!r} already exists in the Brood Chamber."
                )
            await self._trail.record(event)
            self._tasks[task.id] = task
            self._questions[question.id] = question

    async def update_question(self, task: Task, question: Question, event: TaskEvent) -> None:
        """Write `task` and `question`'s new values with `event`; see TaskStore.update_question."""
        check_task_event(task, event)
        async with self._lock:
            if task.id not in self._tasks:
                raise TaskNotFoundError(task.id)
            if question.id not in self._questions:
                raise QuestionNotFoundError(question.id)
            await self._trail.record(event)
            self._tasks[task.id] = task
            self._questions[question.id] = question

    async def get_question(self, question_id: MessageId) -> Question:
        """Return the stored question with id `question_id`; see TaskStore.get_question."""
        async with self._lock:
            question = self._questions.get(question_id)
        if question is None:
            raise QuestionNotFoundError(question_id)
        return question

    async def list_questions(
        self, task_id: TaskId | None, status: QuestionStatus | None
    ) -> tuple[Question, ...]:
        """Return every question matching the filters, ordered by (asked_at, id); see TaskStore."""
        async with self._lock:
            questions = list(self._questions.values())
        matches = [
            question
            for question in questions
            if (task_id is None or question.task_id == task_id)
            and (status is None or question.status == status)
        ]
        matches.sort(key=lambda question: (question.asked_at, question.id))
        return tuple(matches)


def _matches_task_filter(task: Task, query: TaskFilter) -> bool:
    """Return whether `task` satisfies every field `query` has set."""
    if query.status is not None and task.status != query.status:
        return False
    return not (query.goal_id is not None and task.goal_id != query.goal_id)
