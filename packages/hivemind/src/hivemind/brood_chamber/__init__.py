"""Persist the task graph and its state machine: the Brood Chamber.

The Brood Chamber is the Hive's task store, holding the task graph and its state machine in
SQLite. Every task the Queen decomposes a goal into lives here for the rest of its life. Phase 2
step 2.4 (task model and state machine), 2.5 (task graph) and the model half of 2.7 (questions)
build the domain layer this face re-exports: the Task and Question models, both state machines,
and the pure graph functions. `hivemind.brood_chamber.store`, `.chamber` and the SQLite/in-memory
stores (roadmap steps 2.6 and 2.8) land in a later phase 2 dispatch and are not part of this face
yet.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.planner, which
    persists the task graph here, and the dispatcher, which reads it. Calls into
    hivemind.common, hivemind.cell (for TaskNeeds and HoneyClearance) and waggle.

Key invariants:
    - Task.status and Question.status only ever move along the edges TRANSITIONS and
      QUESTION_TRANSITIONS list; assert_transition and assert_question_transition are the only
      way anything in the Hive enforces that.
    - TaskGraphDraft is acyclic by construction: a pydantic ValidationError, not a Task or a
      TaskGraphDraft, is what a cyclic or malformed submission produces.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/roadmap.md phase 2 for the work that first populates this package.
    - hivemind.brood_chamber.README for the module-by-module map of this package.

Public API:
    - BroodChamberError, TaskNotFoundError, QuestionNotFoundError, TaskAlreadyExistsError,
      InvalidTransitionError, InvalidGraphError: this subsystem's error tree (errors).
    - TaskStatus, TERMINAL_STATUSES, TRANSITIONS, can_transition, assert_transition, is_terminal:
      the task state machine (task_state).
    - TaskSpec, TaskOutcome, Task, TaskDraft, TaskGraphDraft: the task model and the JSON graph
      file `hive tasks submit` reads (task).
    - is_acyclic_edges, is_acyclic, ready_tasks, descendants: pure functions over a task graph
      (graph).
    - QuestionStatus, AnswerSource, Answer, Question, QUESTION_TRANSITIONS,
      assert_question_transition: the question model and its state machine (questions).
"""

from hivemind.brood_chamber.errors import (
    BroodChamberError,
    InvalidGraphError,
    InvalidTransitionError,
    QuestionNotFoundError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from hivemind.brood_chamber.graph import (
    descendants,
    is_acyclic,
    is_acyclic_edges,
    ready_tasks,
)
from hivemind.brood_chamber.questions import (
    QUESTION_TRANSITIONS,
    Answer,
    AnswerSource,
    Question,
    QuestionStatus,
    assert_question_transition,
)
from hivemind.brood_chamber.task import Task, TaskDraft, TaskGraphDraft, TaskOutcome, TaskSpec
from hivemind.brood_chamber.task_state import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    TaskStatus,
    assert_transition,
    can_transition,
    is_terminal,
)

__all__ = [
    "QUESTION_TRANSITIONS",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "Answer",
    "AnswerSource",
    "BroodChamberError",
    "InvalidGraphError",
    "InvalidTransitionError",
    "Question",
    "QuestionNotFoundError",
    "QuestionStatus",
    "Task",
    "TaskAlreadyExistsError",
    "TaskDraft",
    "TaskGraphDraft",
    "TaskNotFoundError",
    "TaskOutcome",
    "TaskSpec",
    "TaskStatus",
    "assert_question_transition",
    "assert_transition",
    "can_transition",
    "descendants",
    "is_acyclic",
    "is_acyclic_edges",
    "is_terminal",
    "ready_tasks",
]
