"""Persist the task graph and its state machine: the Brood Chamber.

The Brood Chamber is the Hive's task store, holding the task graph and its state machine in
SQLite. Every task the Queen decomposes a goal into lives here for the rest of its life. Phase 2
step 2.4 (task model and state machine), 2.5 (task graph), the model half of 2.7 (questions) and
step 2.6 (the store) build the persistence layer this face re-exports: the Task and Question
models, both state machines, the pure graph functions, and TaskStore with its two implementations,
grouped into the `task` and `store` sub-packages (codingrules 5.6: at most ten modules per
directory). Step 2.8 adds `hivemind.brood_chamber.chamber`, the thin facade (`BroodChamber`) the
Queen calls instead of the state machines and the store directly.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.planner, which
    persists the task graph here, and the dispatcher, which reads it, both through BroodChamber.
    Calls into hivemind.common, hivemind.cell (for TaskNeeds and HoneyClearance), hivemind.pheromone
    (for TaskEvent and PheromoneTrail) and waggle.

Key invariants:
    - Task.status and Question.status only ever move along the edges TRANSITIONS and
      QUESTION_TRANSITIONS list; assert_transition and assert_question_transition are the only
      way anything in the Hive enforces that.
    - TaskGraphDraft is acyclic by construction: a pydantic ValidationError, not a Task or a
      TaskGraphDraft, is what a cyclic or malformed submission produces.
    - Every TaskStore mutation records its TaskEvent(s) in the same transaction as the state
      change (Appendix C rule 3); check_task_event is the guard both implementations call first.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies, and Appendix C
      rule 3 for the same-transaction rule TaskStore's implementations guarantee.
    - .claude/roadmap.md phase 2 for the work that first populates this package.
    - hivemind.brood_chamber.README for the module-by-module map of this package.

Public API:
    - BroodChamberError, TaskNotFoundError, QuestionNotFoundError, TaskAlreadyExistsError,
      InvalidTransitionError, InvalidGraphError: this subsystem's error tree (errors).
    - TaskStatus, TERMINAL_STATUSES, TRANSITIONS, can_transition, assert_transition, is_terminal:
      the task state machine (task.state).
    - TaskSpec, TaskOutcome, Task, TaskDraft, TaskGraphDraft: the task model and the JSON graph
      file `hive tasks submit` reads (task.model).
    - is_acyclic_edges, is_acyclic, ready_tasks, descendants: pure functions over a task graph
      (task.graph).
    - QuestionStatus, AnswerSource, Answer, Question, QUESTION_TRANSITIONS,
      assert_question_transition: the question model and its state machine (questions).
    - TaskFilter, TaskStore, check_task_event: the store protocol and its query/guard
      (store.protocol).
    - MemoryTaskStore: an in-process TaskStore for tests and demos (store.memory).
    - SqliteTaskStore, apply_brood_chamber_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      TaskStore (store.sqlite).
    - BroodChamber, ChamberIdentity: the public facade the Queen (and, until phase 3, the CLI)
      uses to submit and advance tasks (chamber).
"""

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.errors import (
    BroodChamberError,
    InvalidGraphError,
    InvalidTransitionError,
    QuestionNotFoundError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from hivemind.brood_chamber.questions import (
    QUESTION_TRANSITIONS,
    Answer,
    AnswerSource,
    Question,
    QuestionStatus,
    assert_question_transition,
)
from hivemind.brood_chamber.store import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    MemoryTaskStore,
    SqliteTaskStore,
    TaskFilter,
    TaskStore,
    apply_brood_chamber_migrations,
    check_task_event,
)
from hivemind.brood_chamber.task import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    Task,
    TaskDraft,
    TaskGraphDraft,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
    assert_transition,
    can_transition,
    descendants,
    is_acyclic,
    is_acyclic_edges,
    is_terminal,
    ready_tasks,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "QUESTION_TRANSITIONS",
    "SUBSYSTEM",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "Answer",
    "AnswerSource",
    "BroodChamber",
    "BroodChamberError",
    "ChamberIdentity",
    "InvalidGraphError",
    "InvalidTransitionError",
    "MemoryTaskStore",
    "Question",
    "QuestionNotFoundError",
    "QuestionStatus",
    "SqliteTaskStore",
    "Task",
    "TaskAlreadyExistsError",
    "TaskDraft",
    "TaskFilter",
    "TaskGraphDraft",
    "TaskNotFoundError",
    "TaskOutcome",
    "TaskSpec",
    "TaskStatus",
    "TaskStore",
    "apply_brood_chamber_migrations",
    "assert_question_transition",
    "assert_transition",
    "can_transition",
    "check_task_event",
    "descendants",
    "is_acyclic",
    "is_acyclic_edges",
    "is_terminal",
    "ready_tasks",
]
