"""Define BroodChamberError and the Brood Chamber's own error tree.

The Brood Chamber (`hivemind.brood_chamber`) is the Hive's task store: the task graph, the task
and question state machines, and their persistence. This module holds the ways a caller can misuse
that store on purpose: looking up a Task or a Question that does not exist, submitting a Task id
that already exists, or asking either state machine (`task_state.TRANSITIONS`,
`questions.QUESTION_TRANSITIONS`) for an edge it does not have. Every one of these is
`BroodChamberError`, the subsystem's own root, so a caller several layers up can catch one name and
know it caught anything the Brood Chamber itself raised on purpose (codingrules section 10).

`InvalidTransitionError` is shared by both state machines rather than split into a Task- and a
Question-specific class: the two machines (`task_state.py`, `questions.py`) never call each other,
but they would otherwise duplicate an identical error shape, and codingrules section 10 asks for
one root per *failure*, not one class per caller. It takes `subject_id` rather than `task_id`
because the id it carries is a Task's id when `task_state.assert_transition` raises it and a
Question's id when `questions.assert_question_transition` does; naming the parameter `task_id`
would misdescribe the second case.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by `hivemind.brood_chamber.
    task_state`, `hivemind.brood_chamber.questions`, `hivemind.brood_chamber.task` (validators) and
    the store the later phase 2 steps add (`hivemind.brood_chamber.store`,
    `hivemind.brood_chamber.sqlite`, `hivemind.brood_chamber.memory`). Imported by every layer
    above that submits, reads or advances a task or a question.

Key invariants:
    - Every BroodChamberError subclass sets its own `code`; none shares a code with another.
    - TaskNotFoundError, QuestionNotFoundError and TaskAlreadyExistsError subclass one of
      `hivemind.common.errors`' six base categories (NotFoundError, ConflictError) because a
      missing or duplicate id maps cleanly onto one of those; InvalidGraphError does not, because
      a cyclic or malformed task graph is specific to `hivemind.brood_chamber.task.TaskGraphDraft`
      and no base category fits it.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError, NotFoundError and ConflictError, the roots this
      module's classes descend from.
    - hivemind.brood_chamber.task_state for TRANSITIONS, the table InvalidTransitionError reports.
    - hivemind.brood_chamber.questions for QUESTION_TRANSITIONS, the table InvalidTransitionError
      also reports.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import ConflictError, HiveMindError, NotFoundError

__all__ = [
    "BroodChamberError",
    "InvalidGraphError",
    "InvalidTransitionError",
    "QuestionNotFoundError",
    "TaskAlreadyExistsError",
    "TaskNotFoundError",
]


class BroodChamberError(HiveMindError):
    """Root of every error `hivemind.brood_chamber` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.brood_chamber.error"


class TaskNotFoundError(NotFoundError):
    """Raise when a lookup by `TaskId` finds no matching Task in the Brood Chamber."""

    code: ClassVar[str] = "hivemind.brood_chamber.task_not_found"

    def __init__(self, task_id: str) -> None:
        """Build the error for a missing Task.

        Args:
            task_id: The `TaskId` that was looked up and not found.
        """
        super().__init__(f"No task with id {task_id!r} exists in the Brood Chamber.")
        self.task_id = task_id


class QuestionNotFoundError(NotFoundError):
    """Raise when a lookup by `MessageId` finds no matching Question in the Brood Chamber."""

    code: ClassVar[str] = "hivemind.brood_chamber.question_not_found"

    def __init__(self, question_id: str) -> None:
        """Build the error for a missing Question.

        Args:
            question_id: The question's `MessageId` that was looked up and not found.
        """
        super().__init__(f"No question with id {question_id!r} exists in the Brood Chamber.")
        self.question_id = question_id


class TaskAlreadyExistsError(ConflictError):
    """Raise when a submission names a `TaskId` the Brood Chamber already holds."""

    code: ClassVar[str] = "hivemind.brood_chamber.task_exists"

    def __init__(self, task_id: str) -> None:
        """Build the error for a duplicate Task id.

        Args:
            task_id: The `TaskId` a submission tried to reuse.
        """
        super().__init__(f"A task with id {task_id!r} already exists in the Brood Chamber.")
        self.task_id = task_id


class InvalidTransitionError(ConflictError):
    """Raise when a state machine is asked for an edge its transition table does not have.

    Shared by `task_state.assert_transition` (Task) and `questions.assert_question_transition`
    (Question); see the module docstring for why `subject_id` is generic rather than `task_id`.
    """

    code: ClassVar[str] = "hivemind.brood_chamber.invalid_transition"

    def __init__(self, from_status: Enum, to_status: Enum, subject_id: str | None = None) -> None:
        """Build the error for a forbidden transition.

        Args:
            from_status: The state the machine was in.
            to_status: The state a caller asked to move to.
            subject_id: The Task's or Question's id, when the caller has it, folded into the
                message so the failure is debuggable without a stack trace.
        """
        # A missing subject_id still produces a full sentence; codingrules section 10 wants the
        # ids needed to debug, not a placeholder, so the clause is only added when one is known.
        subject = f" for {subject_id}" if subject_id is not None else ""
        message = (
            f"Cannot transition{subject} from {from_status.name} to {to_status.name}: no such "
            "edge exists in the state machine."
        )
        super().__init__(message)
        self.from_status = from_status
        self.to_status = to_status
        self.subject_id = subject_id


class InvalidGraphError(BroodChamberError):
    """Raise when a task graph is malformed: a cycle, an unknown key, or a duplicate key.

    `hivemind.brood_chamber.task.TaskGraphDraft` is a pydantic model, so its own validators raise
    `ValueError` (pydantic wraps it into a `ValidationError`), the ordinary boundary-validation
    path for anything read from JSON (codingrules section 9) -- not this class. This is the
    domain-error counterpart `hivemind.brood_chamber.chamber` (roadmap step 2.8) raises once a
    graph has already passed pydantic validation but still fails a check made outside it, so a
    caller of the Brood Chamber's public API catches one typed error either way.
    """

    code: ClassVar[str] = "hivemind.brood_chamber.invalid_graph"
