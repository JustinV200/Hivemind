"""Decompose a goal into the task graph the Brood Chamber persists.

The Brood Chamber is the Hive's task store; this package is how the Queen turns a goal into the
graph it then persists there. `schema.py` is the JSON schema a model fills (`PlanSchema`,
`PlannedTask`, `PlannedPostcondition` -- roadmap step 3.18's "the planner emits acceptance for
every subtask"); `plan.py` (`plan_goal`) renders `decompose_goal.md`, asks a model for that schema
through the structured-output ladder, and converts the reply into a validated
`hivemind.brood_chamber.TaskGraphDraft`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles decomposing a goal into the task graph the Brood Chamber persists. Called by
    `hivemind.queen.queen.Queen.submit_goal`.

Key invariants:
    - `plan_goal` never returns a graph missing acceptance criteria on any subtask, or a cyclic
      one: both fail inside `hivemind.brood_chamber.TaskGraphDraft`'s own construction.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/roadmap.md phase 3 steps 3.18 and 3.20 for the work that populates it.

Public API (roadmap step 3.20):
    - MAX_ACCEPTANCE_ITEMS, MAX_DEPENDS_ON, MAX_KEY_CHARS, MAX_OBJECTIVE_CHARS, MAX_PLAN_TASKS,
      MAX_TITLE_CHARS, MIN_ACCEPTANCE_ITEMS, MIN_PLAN_TASKS, PlanSchema, PlannedPostcondition,
      PlannedTask: the model-facing schema (schema).
    - PLANNER_MAX_OUTPUT_TOKENS, PlannerError, plan_goal: turning a goal into a TaskGraphDraft
      (plan).
"""

from hivemind.queen.planner.plan import PLANNER_MAX_OUTPUT_TOKENS, PlannerError, plan_goal
from hivemind.queen.planner.schema import (
    MAX_ACCEPTANCE_ITEMS,
    MAX_DEPENDS_ON,
    MAX_KEY_CHARS,
    MAX_OBJECTIVE_CHARS,
    MAX_PLAN_TASKS,
    MAX_TITLE_CHARS,
    MIN_ACCEPTANCE_ITEMS,
    MIN_PLAN_TASKS,
    PlannedPostcondition,
    PlannedTask,
    PlanSchema,
)

__all__ = [
    "MAX_ACCEPTANCE_ITEMS",
    "MAX_DEPENDS_ON",
    "MAX_KEY_CHARS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_PLAN_TASKS",
    "MAX_TITLE_CHARS",
    "MIN_ACCEPTANCE_ITEMS",
    "MIN_PLAN_TASKS",
    "PLANNER_MAX_OUTPUT_TOKENS",
    "PlanSchema",
    "PlannedPostcondition",
    "PlannedTask",
    "PlannerError",
    "plan_goal",
]
