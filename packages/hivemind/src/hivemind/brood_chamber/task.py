"""Define Task, TaskSpec, TaskOutcome and the JSON graph file a human submits.

A Task is one unit of work the Queen decomposes a goal into. This module holds its whole shape:
`TaskSpec` is what a task is asked to do (title, objective, acceptance criteria, the `TaskNeeds`
its Cell must meet, and the ids of tasks it depends on); `TaskOutcome` is how it ended, recorded
once it reaches a terminal `TaskStatus` (`hivemind.brood_chamber.task_state`); `Task` is the whole
record the Brood Chamber stores, spec plus current status plus placement plus outcome, immutable
like every boundary value in the Hive (state changes produce a new `Task` via `model_copy`,
codingrules section 8.5) rather than being mutated in place. `TaskDraft` and `TaskGraphDraft` are a
second, smaller model family: the JSON file a human hands to `hive tasks submit` (roadmap step
2.9), naming its tasks by short string keys instead of `TaskId`s that do not exist yet, because no
`Task` can be minted until the whole graph has been checked for cycles and unknown keys.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read and written by
    `hivemind.brood_chamber.chamber` (roadmap step 2.8) and `hivemind.brood_chamber.store`
    (roadmap step 2.6), which is where a `Task`'s immutability is exercised through `model_copy`.
    `TaskGraphDraft` is read by `hivemind.cli.tasks` (`hive tasks submit FILE`) and turned into
    `Task`s by `BroodChamber.submit`. Calls into `hivemind.brood_chamber.task_state`,
    `hivemind.brood_chamber.graph` (for `TaskGraphDraft`'s cycle check) and `hivemind.cell` (for
    `TaskNeeds`, `HoneyClearance`) only.

Key invariants:
    - Task.outcome is set if and only if Task.status is terminal (TaskStatus.is_terminal), and
      when set, outcome.status equals status.
    - Task.pending_question_id is set if and only if Task.status is TaskStatus.BLOCKED.
    - Task.warden_id and Task.cell_id are both set or both None, and are set exactly when status
      is one of ASSIGNED, RUNNING, BLOCKED, PAUSED (the "placed" statuses).
    - A Task never appears in its own spec.depends_on, and Task.updated_at is never earlier than
      Task.created_at.
    - TaskOutcome.status is always terminal, and TaskOutcome.verified_by (a WardenId) is set if
      and only if status is SUCCEEDED: a Warden, never the Worker that did the work, verifies it.
    - TaskGraphDraft.tasks has unique keys, every depends_on names a key that exists and is not
      its own, and the whole graph is acyclic (checked with hivemind.brood_chamber.graph.
      is_acyclic_edges); the first draft in the tuple is the goal, and every Task
      hivemind.brood_chamber.chamber mints from the graph gets goal_id set to the first task's
      minted id (the chamber does the minting; this module only fixes which draft is first).

See Also:
    - .claude/codingrules.md section 8.5 for the immutable-value rule Task follows.
    - .claude/roadmap.md phase 2 step 2.4 for the task model and step 2.9 for `hive tasks submit`.
    - hivemind.brood_chamber.task_state for TaskStatus and the transition table Task's status
      moves through.
    - hivemind.brood_chamber.graph for is_acyclic_edges, ready_tasks and descendants, the pure
      functions this module's TaskGraphDraft and its callers build on.
    - hivemind.cell for TaskNeeds and HoneyClearance.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.brood_chamber.graph import is_acyclic_edges
from hivemind.brood_chamber.task_state import TERMINAL_STATUSES, TaskStatus
from hivemind.cell import HoneyClearance, TaskNeeds
from waggle.messages import Postcondition
from waggle.messages.base import (
    CellIdField,
    MessageIdField,
    TaskIdField,
    UtcDatetime,
    WardenIdField,
)

MAX_TITLE_CHARS = 200  # A one-line summary; the objective carries the detail.
MAX_OBJECTIVE_CHARS = 8_000  # A few pages: enough to brief a Worker fully, never a whole document.
MIN_ACCEPTANCE_ITEMS = 1  # A task with no acceptance criteria could never be verified.
MAX_ACCEPTANCE_ITEMS = 32  # More than this is really several tasks stapled together.
MAX_DEPENDENCIES = 64  # A wide fan-in is a planning smell before it is a performance problem.
MAX_SUMMARY_CHARS = 2_000  # A paragraph or two: enough to explain an outcome, never a transcript.
MAX_ARTIFACTS = 64  # Paths or urls an outcome points to; more belongs in the objects it names.
MAX_ARTIFACT_CHARS = 4_096  # PATH_MAX-scale, matching waggle.messages.base.MAX_PATH_CHARS's order.
MIN_ATTEMPT = 1  # A task's first attempt is numbered 1, not 0.
MAX_GRAPH_TASKS = 256  # A generous single submission; a larger plan is several submissions.
KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"  # A short, url-safe, human-chosen draft key.

# The statuses in which a task holds a placement: chosen a Cell and a Warden, but not necessarily
# still running on it (BLOCKED and PAUSED keep the placement so resuming does not need to re-place).
_PLACED_STATUSES = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PAUSED}
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
    "Task",
    "TaskDraft",
    "TaskGraphDraft",
    "TaskOutcome",
    "TaskSpec",
]


class TaskSpec(BaseModel):
    """What a task is asked to do: its brief, its acceptance criteria, its needs and dependencies.

    Immutable once a Task carries it; a re-plan produces a new Task with a new TaskSpec rather
    than editing one in place (codingrules section 8.5).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS, description="A one-line summary.")
    objective: str = Field(
        min_length=1, max_length=MAX_OBJECTIVE_CHARS, description="The full brief for the task."
    )
    acceptance: tuple[Postcondition, ...] = Field(
        min_length=MIN_ACCEPTANCE_ITEMS,
        max_length=MAX_ACCEPTANCE_ITEMS,
        description="The criteria Capping checks before the task can be marked SUCCEEDED.",
    )
    needs: TaskNeeds = Field(
        default_factory=TaskNeeds, description="What the task requires from its Cell."
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C1, description="The data-sensitivity label of the task itself."
    )
    depends_on: tuple[TaskIdField, ...] = Field(
        default=(),
        max_length=MAX_DEPENDENCIES,
        description="Ids of tasks that must SUCCEED before this one may start.",
    )

    @field_validator("depends_on")
    @classmethod
    def _depends_on_has_no_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a depends_on tuple that names the same task id more than once."""
        if len(set(value)) != len(value):
            raise ValueError(f"TaskSpec.depends_on contains a duplicate id: {value!r}.")
        return value


class TaskOutcome(BaseModel):
    """How a task ended; recorded once its status is terminal (TaskStatus.is_terminal)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TaskStatus = Field(description="Must be terminal: SUCCEEDED, FAILED or CANCELLED.")
    summary: str = Field(
        min_length=1, max_length=MAX_SUMMARY_CHARS, description="What happened, in plain prose."
    )
    artifacts: tuple[Annotated[str, Field(max_length=MAX_ARTIFACT_CHARS)], ...] = Field(
        default=(), max_length=MAX_ARTIFACTS, description="Paths or urls the task produced."
    )
    verified_by: WardenIdField | None = Field(
        default=None,
        description="The Warden that ran acceptance checks; required when status is SUCCEEDED, "
        "because the Worker that did the work never verifies its own success.",
    )
    spend_usd: float = Field(
        default=0.0, ge=0, description="What this attempt cost, in US dollars."
    )

    @field_validator("status")
    @classmethod
    def _status_is_terminal(cls, value: TaskStatus) -> TaskStatus:
        """Reject a non-terminal status: an outcome only exists once a task has finished."""
        if value not in TERMINAL_STATUSES:
            raise ValueError(f"TaskOutcome.status must be terminal, got {value.name}.")
        return value

    @model_validator(mode="after")
    def _verified_by_required_iff_succeeded(self) -> TaskOutcome:
        """Require verified_by exactly when status is SUCCEEDED."""
        if self.status is TaskStatus.SUCCEEDED and self.verified_by is None:
            raise ValueError("TaskOutcome.status=SUCCEEDED requires verified_by (a Warden).")
        if self.status is not TaskStatus.SUCCEEDED and self.verified_by is not None:
            raise ValueError(
                f"TaskOutcome.verified_by is only set when status is SUCCEEDED, got "
                f"{self.status.name}."
            )
        return self


class Task(BaseModel):
    """The whole record of one task: its spec, its current status, its placement and its outcome.

    Immutable: a state change is a new Task built with `model_copy(update=...)`
    (`hivemind.brood_chamber.chamber`, roadmap step 2.8), never an in-place mutation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TaskIdField = Field(description="This task's own id.")
    goal_id: TaskIdField = Field(
        description="The first task's id in the TaskGraphDraft this task was minted from."
    )
    spec: TaskSpec = Field(description="What this task is asked to do.")
    status: TaskStatus = Field(description="Where this task is in task_state.TRANSITIONS.")
    attempt: int = Field(
        default=MIN_ATTEMPT, ge=MIN_ATTEMPT, description="Which attempt this is; the first is 1."
    )
    warden_id: WardenIdField | None = Field(
        default=None, description="The Warden supervising this task's Cell, once placed."
    )
    cell_id: CellIdField | None = Field(
        default=None, description="The Cell this task runs on, once placed."
    )
    created_at: UtcDatetime = Field(description="When this task was first submitted.")
    updated_at: UtcDatetime = Field(description="When this record was last written.")
    last_summary: str | None = Field(
        default=None,
        max_length=MAX_SUMMARY_CHARS,
        description="The most recent progress summary, or None before the first one.",
    )
    fraction_done: float | None = Field(
        default=None, ge=0, le=1, description="The Worker's own progress estimate, when reported."
    )
    outcome: TaskOutcome | None = Field(
        default=None, description="Set once status is terminal; None otherwise."
    )
    pending_question_id: MessageIdField | None = Field(
        default=None, description="Set exactly when status is BLOCKED: the question awaiting reply."
    )

    @model_validator(mode="after")
    def _validate_task_consistency(self) -> Task:
        """Run every cross-field invariant; split into helpers so each stays small and readable."""
        _check_outcome_matches_status(self)
        _check_pending_question_matches_status(self)
        _check_placement_matches_status(self)
        _check_no_self_dependency(self)
        _check_updated_at_after_created_at(self)
        return self


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
    needs: TaskNeeds = Field(
        default_factory=TaskNeeds, description="What the task requires from its Cell."
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C1, description="The data-sensitivity label of the task itself."
    )
    depends_on: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_DEPENDENCIES,
        description="Keys of other drafts in the same TaskGraphDraft that must SUCCEED first.",
    )


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


def _check_outcome_matches_status(task: Task) -> None:
    """Require outcome set iff status is terminal, and outcome.status to match status when set."""
    if task.status in TERMINAL_STATUSES:
        if task.outcome is None:
            raise ValueError(f"Task {task.id} is {task.status.name} and requires an outcome.")
        if task.outcome.status is not task.status:
            raise ValueError(
                f"Task {task.id} outcome.status ({task.outcome.status.name}) does not match "
                f"task.status ({task.status.name})."
            )
    elif task.outcome is not None:
        raise ValueError(
            f"Task {task.id} is {task.status.name} (non-terminal) and must not carry an outcome."
        )


def _check_pending_question_matches_status(task: Task) -> None:
    """Require pending_question_id set if and only if status is BLOCKED."""
    if task.status is TaskStatus.BLOCKED and task.pending_question_id is None:
        raise ValueError(f"Task {task.id} is BLOCKED and requires pending_question_id.")
    if task.status is not TaskStatus.BLOCKED and task.pending_question_id is not None:
        raise ValueError(f"Task {task.id} is not BLOCKED and must not carry pending_question_id.")


def _check_placement_matches_status(task: Task) -> None:
    """Require warden_id and cell_id together, set exactly when status is a placed status."""
    placed = task.status in _PLACED_STATUSES
    both_set = task.warden_id is not None and task.cell_id is not None
    both_unset = task.warden_id is None and task.cell_id is None
    if not both_set and not both_unset:
        raise ValueError(f"Task {task.id} must have warden_id and cell_id both set or both None.")
    if placed and not both_set:
        raise ValueError(
            f"Task {task.id} is {task.status.name} and requires warden_id and cell_id."
        )
    if not placed and not both_unset:
        raise ValueError(f"Task {task.id} is {task.status.name} and must not carry a placement.")


def _check_no_self_dependency(task: Task) -> None:
    """Reject a task whose own id appears in its spec's depends_on."""
    if task.id in task.spec.depends_on:
        raise ValueError(f"Task {task.id} cannot depend on itself.")


def _check_updated_at_after_created_at(task: Task) -> None:
    """Reject updated_at earlier than created_at."""
    if task.updated_at < task.created_at:
        raise ValueError(
            f"Task {task.id} updated_at ({task.updated_at}) precedes created_at "
            f"({task.created_at})."
        )


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
