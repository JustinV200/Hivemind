"""Re-export the task model, its state machine and the pure graph functions: the task package.

A Task (the Hive's one unit of work, `hivemind.brood_chamber.task.model.Task`) is defined across
three modules split by responsibility: `model` holds the pydantic shapes (`TaskSpec`, `TaskOutcome`,
`Task`, and the `TaskDraft`/`TaskGraphDraft` JSON-submission family), `state` holds the state
machine (`TaskStatus` and `TRANSITIONS`), and `graph` holds the pure functions over a task graph
(`is_acyclic_edges`, `is_acyclic`, `ready_tasks`, `descendants`). This file is the package's face: a
caller writes `from hivemind.brood_chamber.task import Task` without knowing the split, while every
name stays defined in the module that names it (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber`. Used by
    `hivemind.brood_chamber.chamber` (roadmap step 2.8), `hivemind.brood_chamber.store` and
    `hivemind.brood_chamber.__init__`, the subsystem's own face. Calls into nothing outside its own
    three modules and `hivemind.cell`/`waggle`, which those modules import directly.

Key invariants:
    - This file holds re-exports and __all__ only; the three modules behind it are where every
      name is actually defined.
    - Sibling modules inside `hivemind.brood_chamber` import `model`, `state` and `graph` by their
      full module path (`hivemind.brood_chamber.task.state`, and so on), never through this face,
      so this package can never be part of a circular import.

See Also:
    - hivemind.brood_chamber.task.model, hivemind.brood_chamber.task.state and
      hivemind.brood_chamber.task.graph for the definitions behind this package's public API.
    - hivemind.brood_chamber for the subsystem face this package's own face feeds.

Public API:
    - TaskSpec, TaskOutcome, Task, TaskDraft, TaskGraphDraft, and their field bounds
      (MAX_TITLE_CHARS, MAX_OBJECTIVE_CHARS, MIN_ACCEPTANCE_ITEMS, MAX_ACCEPTANCE_ITEMS,
      MAX_DEPENDENCIES, MAX_SUMMARY_CHARS, MAX_ARTIFACTS, MAX_ARTIFACT_CHARS, MIN_ATTEMPT,
      MAX_GRAPH_TASKS, KEY_PATTERN): the task model (model).
    - TaskStatus, TERMINAL_STATUSES, TRANSITIONS, can_transition, assert_transition, is_terminal:
      the task state machine (state).
    - is_acyclic_edges, is_acyclic, ready_tasks, descendants: pure functions over a task graph
      (graph).
"""

from hivemind.brood_chamber.task.graph import descendants, is_acyclic, is_acyclic_edges, ready_tasks
from hivemind.brood_chamber.task.model import (
    KEY_PATTERN,
    MAX_ACCEPTANCE_ITEMS,
    MAX_ARTIFACT_CHARS,
    MAX_ARTIFACTS,
    MAX_DEPENDENCIES,
    MAX_GRAPH_TASKS,
    MAX_OBJECTIVE_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    MIN_ACCEPTANCE_ITEMS,
    MIN_ATTEMPT,
    Task,
    TaskDraft,
    TaskGraphDraft,
    TaskOutcome,
    TaskSpec,
)
from hivemind.brood_chamber.task.state import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    TaskStatus,
    assert_transition,
    can_transition,
    is_terminal,
)

__all__ = [
    "KEY_PATTERN",
    "MAX_ACCEPTANCE_ITEMS",
    "MAX_ARTIFACTS",
    "MAX_ARTIFACT_CHARS",
    "MAX_DEPENDENCIES",
    "MAX_GRAPH_TASKS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_SUMMARY_CHARS",
    "MAX_TITLE_CHARS",
    "MIN_ACCEPTANCE_ITEMS",
    "MIN_ATTEMPT",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "Task",
    "TaskDraft",
    "TaskGraphDraft",
    "TaskOutcome",
    "TaskSpec",
    "TaskStatus",
    "assert_transition",
    "can_transition",
    "descendants",
    "is_acyclic",
    "is_acyclic_edges",
    "is_terminal",
    "ready_tasks",
]
