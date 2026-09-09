"""Define TaskStore, the Brood Chamber's task-and-question persistence protocol.

The Brood Chamber (`hivemind.brood_chamber`) is the Hive's task store: every `Task`
(`hivemind.brood_chamber.task.model`) the Queen decomposes a goal into, and every `Question`
(`hivemind.brood_chamber.questions`) raised against one, lives here for the rest of its life. This
module fixes the one seam every implementation must honour (codingrules section 8.1): a `Task` or
`Question` mutation and the `TaskEvent` (`hivemind.pheromone`) that records it on the Pheromone
Trail commit together, in the same transaction, or neither commits at all (Appendix C rule 3, "the
trail can never disagree with the store"). `TaskFilter` is `list_tasks`'s query shape.
`check_task_event` is the one guard both implementations (`hivemind.brood_chamber.store.memory.
MemoryTaskStore`, `hivemind.brood_chamber.store.sqlite.SqliteTaskStore`) call before writing, so a
bug inside the Brood Chamber can never file an event under the wrong task's `subject_id` or the
wrong event family.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by
    `hivemind.brood_chamber.store.memory` and `hivemind.brood_chamber.store.sqlite`; used by
    `hivemind.brood_chamber.chamber` (roadmap step 2.8), the only caller that decides *when* a
    transition happens. Calls into `hivemind.brood_chamber.task.model`, `hivemind.brood_chamber.
    questions`, `hivemind.brood_chamber.task.state`, `hivemind.common.errors` and
    `hivemind.pheromone` (`TaskEvent`) only.

Key invariants:
    - Every mutation method's event(s) commit together with the state change they describe, or
      neither commits at all (Appendix C rule 3): a caller can rely on the trail never
      disagreeing with the store.
    - `check_task_event(task, event)` is called by both implementations before any write; a
      mismatched `(task, event)` pair never reaches storage.
    - `insert_tasks` writes nothing at all when `tasks` and `events` differ in length, when any
      task id already exists, or when any `(task, event)` pair fails `check_task_event`.

See Also:
    - .claude/codingrules.md Appendix C rule 3 for the same-transaction rule this protocol
      guarantees, and section 8.1 for the Protocol-at-every-seam rule this module follows.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions the two implementations follow.
    - hivemind.brood_chamber.store.memory and hivemind.brood_chamber.store.sqlite for the two
      implementations.
    - hivemind.pheromone for TaskEvent, PheromoneTrail and insert_event, the primitives this
      protocol's implementations build on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber.questions import Question, QuestionStatus
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone import TaskEvent
from waggle.ids import MessageId, TaskId
from waggle.messages.base import TaskIdField

MIN_TASK_FILTER_LIMIT = 1  # A filter must return something or nothing, never a negative count.
MAX_TASK_FILTER_LIMIT = 10_000  # Matches pheromone.trail.protocol.MAX_QUERY_LIMIT's page ceiling.
DEFAULT_TASK_FILTER_LIMIT = (
    1_000  # Generous for a human `hive tasks list`, small enough to stay fast.
)

__all__ = [
    "DEFAULT_TASK_FILTER_LIMIT",
    "MAX_TASK_FILTER_LIMIT",
    "MIN_TASK_FILTER_LIMIT",
    "TaskFilter",
    "TaskStore",
    "check_task_event",
]


class TaskFilter(BaseModel):
    """A filtered, bounded read of the Brood Chamber's tasks (`TaskStore.list_tasks`).

    Every field is optional; an unset field applies no filter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TaskStatus | None = Field(default=None, description="Only tasks in this status.")
    goal_id: TaskIdField | None = Field(
        default=None, description="Only tasks whose goal_id equals this."
    )
    limit: int = Field(
        default=DEFAULT_TASK_FILTER_LIMIT,
        ge=MIN_TASK_FILTER_LIMIT,
        le=MAX_TASK_FILTER_LIMIT,
        description="Maximum tasks to return; the store truncates rather than raising.",
    )


def check_task_event(task: Task, event: TaskEvent) -> None:
    """Require that `event` is well-formed to record alongside a write to `task`.

    Both `TaskStore` implementations call this before writing, so a Brood Chamber bug can never
    file an event under the wrong task: a mismatched `(task, event)` pair fails loudly instead of
    silently corrupting the trail.

    Args:
        task: The task the event is being recorded alongside.
        event: The event about to be recorded.

    Raises:
        InvariantViolationError: `event.subject_id` is not `task.id`, or `event.family` is not
            `"task"`.
    """
    # subject_id first: the more common mistake is building the event from the wrong task, and
    # this order reports that before the (rarer) wrong-family case.
    if event.subject_id != task.id:
        raise InvariantViolationError(
            f"event {event.id} has subject_id {event.subject_id!r}, expected task id {task.id!r}."
        )
    if event.family != "task":
        raise InvariantViolationError(
            f"event {event.id} has family {event.family!r}, expected 'task' for task {task.id!r}."
        )


class TaskStore(Protocol):
    """Persist Tasks and Questions, each mutation atomic with its Pheromone Trail event(s).

    Implementations (`hivemind.brood_chamber.store.memory.MemoryTaskStore`,
    `hivemind.brood_chamber.store.sqlite.SqliteTaskStore`) must be safe to call concurrently. Every
    mutation method's event commits together with the state change it describes, or neither
    commits at all (Appendix C rule 3): a caller can rely on the trail never disagreeing with the
    store.
    """

    async def insert_tasks(self, tasks: Sequence[Task], events: Sequence[TaskEvent]) -> None:
        """Insert every task in `tasks`, each with its own event, atomically.

        Args:
            tasks: The tasks to insert; every id must be new to the store.
            events: One `task.submitted` event per task, same length and order as `tasks`.

        Returns:
            None, once every task and every event is durably recorded.

        Raises:
            InvariantViolationError: `tasks` and `events` are different lengths, or `check_task_
                event` rejects one of the pairs; nothing is written.
            TaskAlreadyExistsError: Any id in `tasks` already exists in the store; nothing in this
                call is written, including tasks whose id was not a duplicate.
        """
        ...

    async def update_task(self, task: Task, event: TaskEvent) -> None:
        """Replace the stored task with the same id as `task`, recording `event` alongside it.

        Args:
            task: The task's new value; `task.id` selects the row to replace.
            event: The event describing this change.

        Returns:
            None, once the update and the event are durably recorded together.

        Raises:
            InvariantViolationError: `check_task_event` rejects `(task, event)`; nothing is
                written.
            TaskNotFoundError: No task with `task.id` exists in the store; nothing is written.
        """
        ...

    async def get_task(self, task_id: TaskId) -> Task:
        """Return the stored task with id `task_id`.

        Args:
            task_id: The task to look up.

        Returns:
            The matching Task.

        Raises:
            TaskNotFoundError: No task with `task_id` exists in the store.
        """
        ...

    async def list_tasks(self, query: TaskFilter) -> tuple[Task, ...]:
        """Return every task matching `query`, ordered by `(created_at, id)`.

        Args:
            query: The filters and limit to apply.

        Returns:
            At most `query.limit` tasks satisfying every set field of `query`, ordered by
            `(created_at, id)` ascending.
        """
        ...

    async def insert_question(self, task: Task, question: Question, event: TaskEvent) -> None:
        """Write `task`'s new value, `question` and `event`, atomically.

        Used when a task moves to BLOCKED: the task's `status` and `pending_question_id` change,
        a new Question row is created, and one `task.blocked` event is recorded, all together.

        Args:
            task: The task's new value, already moved to BLOCKED.
            question: The new question to store; its id must be new to the store.
            event: The event describing the transition.

        Returns:
            None, once all three writes are durably recorded together.

        Raises:
            InvariantViolationError: `check_task_event` rejects `(task, event)`; nothing is
                written.
            TaskNotFoundError: No task with `task.id` exists in the store; nothing is written.
            hivemind.common.errors.ConflictError: `question.id` already exists in the store;
                nothing is written.
        """
        ...

    async def update_question(self, task: Task, question: Question, event: TaskEvent) -> None:
        """Write `task`'s new value and `question`'s new value, with `event`, atomically.

        Used when a question is answered or withdrawn: the task moves back to RUNNING, the
        question's `status` (and, for an answer, `answer`) changes, and one event is recorded.

        Args:
            task: The task's new value, already moved back to RUNNING.
            question: The question's new value.
            event: The event describing the transition.

        Returns:
            None, once both writes and the event are durably recorded together.

        Raises:
            InvariantViolationError: `check_task_event` rejects `(task, event)`; nothing is
                written.
            TaskNotFoundError: No task with `task.id` exists in the store; nothing is written.
            QuestionNotFoundError: No question with `question.id` exists in the store; nothing is
                written.
        """
        ...

    async def get_question(self, question_id: MessageId) -> Question:
        """Return the stored question with id `question_id`.

        Args:
            question_id: The question to look up.

        Returns:
            The matching Question.

        Raises:
            QuestionNotFoundError: No question with `question_id` exists in the store.
        """
        ...

    async def list_questions(
        self, task_id: TaskId | None, status: QuestionStatus | None
    ) -> tuple[Question, ...]:
        """Return every question matching the given filters, ordered by `(asked_at, id)`.

        Args:
            task_id: When set, only questions belonging to this task.
            status: When set, only questions in this status.

        Returns:
            Every matching question, ordered by `(asked_at, id)` ascending; the Queen's inbox
            reads pending questions by passing `status=QuestionStatus.ASKED`.
        """
        ...
