"""Define TaskDraft and TaskGraphDraft: a task graph as it is submitted, before any id exists.

The JSON file a human hands to `hive tasks submit` (roadmap step 2.9), and the shape the Queen's
planner converts a model's plan into (`hivemind.queen.planner.plan`), name their tasks by short
string keys instead of `TaskId`s that do not exist yet, because no `Task` can be minted until the
whole graph has been checked for cycles and unknown keys. `TaskDraft` is one task (a `TaskSpec`
named by its key, `hivemind.brood_chamber.task.model`), and `TaskGraphDraft` the whole graph,
which checks every cross-draft rule once; `hivemind.brood_chamber.chamber.submit` mints the ids.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by `hivemind.cli.tasks` (`hive
    tasks submit FILE`) and built by `hivemind.queen.planner`; turned into `Task`s by
    `BroodChamber.submit`. Calls into `hivemind.brood_chamber.task.model` (the field bounds and
    the role rule it shares with TaskSpec), `.graph` (is_acyclic_edges), `.goal_set`,
    `.goal_request`, `hivemind.cell` and waggle only.

Key invariants:
    - TaskGraphDraft.tasks has unique keys, every depends_on names a key that exists and is not
      its own, and the whole graph is acyclic (checked with hivemind.brood_chamber.task.graph.
      is_acyclic_edges); the first draft in the tuple is the goal, and every Task
      hivemind.brood_chamber.chamber mints from the graph gets goal_id set to the first task's
      minted id (the chamber does the minting; this module only fixes which draft is first).
    - TaskDraft.role defaults to WorkerRole.DRONE and is one of PLANNABLE_ROLES, exactly as
      TaskSpec.role (`check_role_is_plannable`, one rule for both).

See Also:
    - .claude/roadmap.md phase 2 step 2.9 for `hive tasks submit`.
    - hivemind.brood_chamber.task.model for TaskSpec, what every draft becomes.
    - hivemind.brood_chamber.task.graph for is_acyclic_edges.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.brood_chamber.task.goal_request import GoalRequestRef, GoalSpendCap
from hivemind.brood_chamber.task.goal_set import GoalCapabilities
from hivemind.brood_chamber.task.graph import is_acyclic_edges
from hivemind.brood_chamber.task.model import (
    MAX_ACCEPTANCE_ITEMS,
    MAX_DEPENDENCIES,
    MAX_LEAVES_ITEMS,
    MAX_OBJECTIVE_CHARS,
    MAX_TITLE_CHARS,
    MIN_ACCEPTANCE_ITEMS,
    check_role_is_plannable,
)
from hivemind.cell import HoneyClearance, RequestOrigin, TaskNeeds
from waggle.messages import PlannedLeaving, Postcondition
from waggle.messages.task import WorkerRole

MAX_GRAPH_TASKS = 256  # A generous single submission; a larger plan is several submissions.
KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"  # A short, url-safe, human-chosen draft key.

__all__ = ["KEY_PATTERN", "MAX_GRAPH_TASKS", "TaskDraft", "TaskGraphDraft"]


class TaskDraft(BaseModel):
    """One task in a TaskGraphDraft: a TaskSpec named by a short key instead of a minted TaskId.

    `depends_on` names other drafts by their `key`, because no `TaskId` exists yet for anything in
    the file; `hivemind.brood_chamber.chamber.submit` (roadmap step 2.8) resolves keys to minted
    ids in one pass, once TaskGraphDraft has confirmed the graph they form is well-formed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=KEY_PATTERN, description="This draft's own name within its file.")
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS, description="A one-line summary.")
    objective: str = Field(
        min_length=1, max_length=MAX_OBJECTIVE_CHARS, description="The full brief for the task."
    )
    acceptance: tuple[Postcondition, ...] = Field(
        min_length=MIN_ACCEPTANCE_ITEMS,
        max_length=MAX_ACCEPTANCE_ITEMS,
        description="The criteria Capping checks before the task can be marked SUCCEEDED.",
    )
    role: WorkerRole = Field(
        default=WorkerRole.DRONE,
        description="Which Worker role the Warden spawns; carried unchanged onto TaskSpec.role.",
    )
    needs: TaskNeeds = Field(
        default_factory=TaskNeeds, description="What the task requires from its Cell."
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C1, description="The data-sensitivity label of the task itself."
    )
    origin: RequestOrigin = Field(
        default=RequestOrigin.HUMAN,
        description="Who asked for this task to exist (roadmap step 5.7a); default HUMAN matches "
        "a hand-written or `hive run` submission. hivemind.brood_chamber.chamber.submission "
        "carries this straight onto the minted Task's TaskSpec.origin.",
    )
    depends_on: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_DEPENDENCIES,
        description="Keys of other drafts in the same TaskGraphDraft that must SUCCEED first.",
    )
    leaves: tuple[PlannedLeaving, ...] = Field(
        default=(),
        max_length=MAX_LEAVES_ITEMS,
        description="What the plan declared should stay on this task's Cell once its lease is "
        "released (roadmap step 5.0b); empty unless the goal itself asks for something to "
        "remain.",
    )
    capabilities: GoalCapabilities = Field(
        default=None,
        description="The capability set of the goal this draft belongs to (roadmap step 10.3); "
        "the chamber carries it onto the minted Task's TaskSpec. None means no device ceiling.",
    )
    # Roadmap step 10.5: carried onto the minted Task's TaskSpec exactly like `capabilities`.
    goal_request_id: GoalRequestRef = None
    spend_cap_usd: GoalSpendCap = None

    @field_validator("role")
    @classmethod
    def _role_is_plannable(cls, value: WorkerRole) -> WorkerRole:
        """Reject a WorkerRole outside PLANNABLE_ROLES; see that constant's own comment."""
        check_role_is_plannable(value, owner="TaskDraft")
        return value


class TaskGraphDraft(BaseModel):
    """The JSON graph file a human hands to `hive tasks submit`: one or more TaskDrafts.

    The first entry in `tasks` is the goal: `hivemind.brood_chamber.chamber.submit` mints it
    first and sets every minted Task's `goal_id` to that first task's id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tasks: tuple[TaskDraft, ...] = Field(
        min_length=1, max_length=MAX_GRAPH_TASKS, description="The drafts, goal (first) onward."
    )

    @model_validator(mode="after")
    def _validate_graph(self) -> TaskGraphDraft:
        """Run every cross-draft invariant; split into helpers so each stays small and readable."""
        _check_unique_keys(self)
        _check_depends_on_keys_exist(self)
        _check_acyclic(self)
        return self


def _check_unique_keys(draft: TaskGraphDraft) -> None:
    """Reject a graph whose drafts reuse the same key."""
    keys = [entry.key for entry in draft.tasks]
    if len(set(keys)) != len(keys):
        raise ValueError(f"TaskGraphDraft.tasks has a duplicate key among: {keys!r}.")


def _check_depends_on_keys_exist(draft: TaskGraphDraft) -> None:
    """Reject a depends_on key that names no draft in the file, or names the draft's own key."""
    known = {entry.key for entry in draft.tasks}
    for entry in draft.tasks:
        for dep in entry.depends_on:
            if dep == entry.key:
                raise ValueError(f"TaskDraft {entry.key!r} cannot depend on itself.")
            if dep not in known:
                raise ValueError(f"TaskDraft {entry.key!r} depends_on unknown key {dep!r}.")


def _check_acyclic(draft: TaskGraphDraft) -> None:
    """Reject a graph whose depends_on edges among the drafts' keys form a cycle."""
    edges = {entry.key: entry.depends_on for entry in draft.tasks}
    if not is_acyclic_edges(edges):
        raise ValueError("TaskGraphDraft.tasks contains a dependency cycle.")
