"""Define PlanSchema: the JSON schema a model fills to decompose one goal into a task graph.

Roadmap step 3.18: "The planner emits `acceptance` for every subtask as a list of postconditions
plus, where nothing machine-checkable exists, a rubric for a judge." `PlannedPostcondition` is a
relaxed, model-facing shape for one acceptance criterion -- the same four fields `waggle.messages.
Postcondition` carries, but without that class's own cross-field validators (argv only for a
command kind, `expected` required for a comparison kind), so a weak model's near-miss on the
`hivemind.llm.ladders.structured` degradation ladder's own JSON-schema rungs is a validation retry
within the ladder rather than a schema mismatch before the ladder even runs. `hivemind.queen.
planner.plan` is the one place that then converts a `PlannedPostcondition` into a real
`Postcondition`, letting *that* conversion raise for a plan the model still got wrong. `PlannedTask`
mirrors `hivemind.brood_chamber.task.model.TaskDraft`'s own fields (key, title, objective,
acceptance, needs, clearance, depends_on), reusing `hivemind.cell.TaskNeeds` and
`hivemind.cell.HoneyClearance` directly since both are already fully defaulted, lenient shapes a
model can fill in without a second, parallel definition. `PlanSchema` is the whole reply: one or
more `PlannedTask`s, the first of which becomes the goal.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's planner
    sub-package (which MAY import `hivemind.llm`). Read by `hivemind.llm.ladders.structured.
    complete_structured` (as the schema a model's reply is validated against) and by
    `hivemind.queen.planner.plan._to_graph_draft` (as the value it converts). Calls into
    `hivemind.cell` (HoneyClearance, TaskNeeds) and `waggle.messages` (PostconditionKind) only.

Key invariants:
    - `PlannedPostcondition` and `PlannedTask` are frozen and forbid extras, like every boundary
      value in this repository, but carry no cross-field validator of their own: `plan.py`'s own
      conversion step is where a truly malformed criterion (e.g. an `argv` on a `FILE_EXISTS`
      criterion) is caught, as a `PlannerError`, not a ladder retry.
    - `PlannedTask.acceptance` never accepts an empty tuple (`min_length=1`): roadmap step 3.18's
      own rule, "every subtask carries at least one acceptance postcondition", is enforced at the
      schema the model fills in, not only after the fact.
    - `PlanSchema.tasks` never accepts an empty tuple either: a plan with no tasks decomposes
      nothing.

See Also:
    - .claude/roadmap.md step 3.18 for "the planner emits acceptance for every subtask".
    - .claude/roadmap.md step 3.20 for this module's own roadmap bullet.
    - waggle.messages.labels for Postcondition and PostconditionKind, the wire shape this schema's
      relaxed model is converted into.
    - hivemind.brood_chamber.task.model for TaskDraft and TaskGraphDraft, what `plan.py` converts
      this schema's values into.
    - hivemind.queen.planner.plan for plan_goal, the one caller of PlanSchema.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance, TaskNeeds
from waggle.messages import PostconditionKind
from waggle.messages.base import MAX_PATH_CHARS
from waggle.messages.labels import MAX_ARGV_ITEM_CHARS, MAX_ARGV_ITEMS, MAX_EXPECTED_CHARS

MAX_KEY_CHARS = 64  # A short, url-safe draft key; matches TaskDraft.key's own scale.
MIN_ACCEPTANCE_ITEMS = 1  # roadmap 3.18: every subtask carries at least one acceptance criterion.
MAX_ACCEPTANCE_ITEMS = 32  # Matches TaskDraft.acceptance's own ceiling.
MAX_TITLE_CHARS = 200  # Matches TaskDraft.title's own scale.
MAX_OBJECTIVE_CHARS = 8_000  # Matches TaskDraft.objective's own scale.
MAX_DEPENDS_ON = 64  # Matches TaskDraft.depends_on's own scale.
MIN_PLAN_TASKS = 1  # A plan that decomposes nothing is not a plan.
MAX_PLAN_TASKS = 64  # A generous single goal's worth of subtasks.

__all__ = [
    "MAX_ACCEPTANCE_ITEMS",
    "MAX_DEPENDS_ON",
    "MAX_KEY_CHARS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_PLAN_TASKS",
    "MAX_TITLE_CHARS",
    "MIN_ACCEPTANCE_ITEMS",
    "MIN_PLAN_TASKS",
    "PlanSchema",
    "PlannedPostcondition",
    "PlannedTask",
]


class PlannedPostcondition(BaseModel):
    """One acceptance criterion, as a model fills it in; converted to a real Postcondition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PostconditionKind = Field(description="What is asserted.")
    subject: str = Field(
        max_length=MAX_PATH_CHARS,
        description="The path, command target or a short label for JUDGE_RUBRIC.",
    )
    argv: tuple[Annotated[str, Field(max_length=MAX_ARGV_ITEM_CHARS)], ...] = Field(
        default=(),
        max_length=MAX_ARGV_ITEMS,
        description="The command as an argument list; only for COMMAND_EXITS_ZERO/TEST_PASSES.",
    )
    expected: str | None = Field(
        default=None,
        max_length=MAX_EXPECTED_CHARS,
        description="What to compare against, or the rubric text for JUDGE_RUBRIC; None when "
        "the kind has nothing to compare.",
    )


class PlannedTask(BaseModel):
    """One subtask, as a model fills it in; converted to a hivemind.brood_chamber.TaskDraft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(
        max_length=MAX_KEY_CHARS, description="This subtask's own short, unique name in the plan."
    )
    title: str = Field(max_length=MAX_TITLE_CHARS, description="A one-line summary.")
    objective: str = Field(max_length=MAX_OBJECTIVE_CHARS, description="The full brief.")
    acceptance: tuple[PlannedPostcondition, ...] = Field(
        min_length=MIN_ACCEPTANCE_ITEMS,
        max_length=MAX_ACCEPTANCE_ITEMS,
        description="How this subtask's own Warden will know it succeeded.",
    )
    needs: TaskNeeds = Field(
        default_factory=TaskNeeds, description="What this subtask requires from its Cell."
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C1, description="This subtask's data-sensitivity label."
    )
    depends_on: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_DEPENDS_ON,
        description="Keys of other subtasks in this same plan that must succeed first.",
    )


class PlanSchema(BaseModel):
    """The model's whole reply: one or more subtasks; the first becomes the goal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tasks: tuple[PlannedTask, ...] = Field(
        min_length=MIN_PLAN_TASKS,
        max_length=MAX_PLAN_TASKS,
        description="The subtasks, goal (first) onward.",
    )
