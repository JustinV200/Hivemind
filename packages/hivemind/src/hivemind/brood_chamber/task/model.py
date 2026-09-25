"""Define Task, TaskSpec and TaskOutcome: one unit of work, what it asks and how it ended.

A Task is one unit of work the Queen decomposes a goal into. This module holds its whole shape:
`TaskSpec` is what a task is asked to do (title, objective, acceptance criteria, which
`WorkerRole` (roadmap steps 6.9/6.10; a Warden spawns a Drone, a Forager or a Scout for it) runs
it, the `TaskNeeds` its Cell must meet, the ids of tasks it depends on, since roadmap step 10.3
the capability set of the goal it was planned from, and since roadmap step 10.5 the goal request
that asked for the goal and the spend cap that request set); `TaskOutcome` is how it ended,
recorded once it reaches a terminal `TaskStatus` (`hivemind.brood_chamber.task.state`); `Task` is
the whole record the Brood Chamber stores, spec plus current status plus placement (with, since
roadmap step 10.3b, the Comb Shield tier the task is bound to on its Cell) plus outcome, immutable
like every boundary value in the Hive (state changes produce a new `Task` via `model_copy`,
codingrules section 8.5) rather than being mutated in place. The drafts a task graph is submitted
as, before any id is minted, live beside this module in `hivemind.brood_chamber.task.draft`;
`check_role_is_plannable` is the one role rule both families share.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read and written by
    `hivemind.brood_chamber.chamber` (roadmap step 2.8) and `hivemind.brood_chamber.store`
    (roadmap step 2.6), which is where a `Task`'s immutability is exercised through `model_copy`.
    Calls into `hivemind.brood_chamber.task.state`, `hivemind.brood_chamber.task.goal_set`
    (GoalCapabilities), `hivemind.brood_chamber.task.goal_request` (GoalRequestRef,
    GoalSpendCap), `hivemind.cell` (for `TaskNeeds`, `HoneyClearance`) and `waggle.messages.task`
    (for `WorkerRole` and `ScoutReport`) only.

Key invariants:
    - Task.outcome is set if and only if Task.status is terminal (TaskStatus.is_terminal), and
      when set, outcome.status equals status.
    - Task.pending_question_id is set if and only if Task.status is TaskStatus.BLOCKED.
    - Task.warden_id and Task.cell_id are both set or both None, and are set exactly when status
      is one of ASSIGNED, RUNNING, BLOCKED, PAUSED (the "placed" statuses).
    - Task.bound_tier is None whenever the task is not placed: a task is bound to its Cell's tier
      at assignment (roadmap step 10.3b) and unbound when it leaves the Cell, so a task placed
      again is re-bound before it runs. A placed task stored before binding existed may carry
      None, which every reader takes as its requested tier.
    - A Task never appears in its own spec.depends_on, and Task.updated_at is never earlier than
      Task.created_at.
    - TaskSpec.role and TaskDraft.role default to WorkerRole.DRONE and are one of PLANNABLE_ROLES
      (DRONE, FORAGER, SCOUT); every other WorkerRole is spawned outside the task graph (a Warden's
      own GuardBee, Undertaker or House Bee) and can never be assigned to a Task. The field is
      additive with a default, so a stored body written before it existed loads as DRONE with no
      migration.
    - TaskOutcome.status is always terminal, and TaskOutcome.verified_by (a WardenId) is set if
      and only if status is SUCCEEDED: a Warden, never the Worker that did the work, verifies it.
      TaskOutcome.scout_report (roadmap step 6.10) is copied from the Warden-verified TaskResult
      that closed the task; set only for a task whose role is SCOUT, on either SUCCEEDED (the
      Scout's own recommendation) or FAILED (an infeasible Scout, capping.autopilot.table).

See Also:
    - .claude/codingrules.md section 8.5 for the immutable-value rule Task follows.
    - .claude/roadmap.md phase 2 step 2.4 for the task model, step 2.9 for `hive tasks submit`,
      and steps 6.9/6.10 for the Forager and Scout roles.
    - hivemind.brood_chamber.task.state for TaskStatus and the transition table Task's status
      moves through.
    - hivemind.brood_chamber.task.draft for TaskDraft and TaskGraphDraft, the submission family.
    - hivemind.cell for TaskNeeds and HoneyClearance.
    - hivemind.queen.planner.schema for PlannedTask, which carries the same role rules a model
      must obey before hivemind.queen.planner.plan converts it into a TaskDraft.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.brood_chamber.task.goal_request import GoalRequestRef, GoalSpendCap
from hivemind.brood_chamber.task.goal_set import GoalCapabilities
from hivemind.brood_chamber.task.state import TERMINAL_STATUSES, TaskStatus
from hivemind.cell import CombShieldLevel, HoneyClearance, RequestOrigin, TaskNeeds
from waggle.messages import PlannedLeaving, Postcondition
from waggle.messages.base import (
    CellIdField,
    MessageIdField,
    TaskIdField,
    UtcDatetime,
    WardenIdField,
)
from waggle.messages.task import ScoutReport, WorkerRole

MAX_TITLE_CHARS = 200  # A one-line summary; the objective carries the detail.
MAX_OBJECTIVE_CHARS = 8_000  # A few pages: enough to brief a Worker fully, never a whole document.
MIN_ACCEPTANCE_ITEMS = 1  # A task with no acceptance criteria could never be verified.
MAX_ACCEPTANCE_ITEMS = 32  # More than this is really several tasks stapled together.
MAX_DEPENDENCIES = 64  # A wide fan-in is a planning smell before it is a performance problem.
MAX_LEAVES_ITEMS = 16  # roadmap 5.0b: matches waggle.messages.task.assignment's own bound.
MAX_SUMMARY_CHARS = 2_000  # A paragraph or two: enough to explain an outcome, never a transcript.
MAX_ARTIFACTS = 64  # Paths or urls an outcome points to; more belongs in the objects it names.
MAX_ARTIFACT_CHARS = 4_096  # PATH_MAX-scale, matching waggle.messages.base.MAX_PATH_CHARS's order.
MIN_ATTEMPT = 1  # A task's first attempt is numbered 1, not 0.

# The statuses in which a task holds a placement: chosen a Cell and a Warden, but not necessarily
# still running on it (BLOCKED and PAUSED keep the placement so resuming does not need to re-place).
_PLACED_STATUSES = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PAUSED}
)

# roadmap steps 6.9/6.10: the only WorkerRole members a Task's own spec may carry. GuardBee,
# Undertaker and House Bee are spawned by a Warden's own autopilot for its own duties, never
# assigned through the task graph (hivemind.queen.dispatcher builds every TaskAssign from a
# TaskSpec.role), so a Task naming one of them could never actually be run. Exported so
# hivemind.queen.planner.schema.PlannedTask (the model-facing shape one layer up) checks the exact
# same set, rather than a second, hand-copied one that could drift.
PLANNABLE_ROLES: frozenset[WorkerRole] = frozenset(
    {WorkerRole.DRONE, WorkerRole.FORAGER, WorkerRole.SCOUT}
)

__all__ = [
    "MAX_ACCEPTANCE_ITEMS",
    "MAX_ARTIFACTS",
    "MAX_ARTIFACT_CHARS",
    "MAX_DEPENDENCIES",
    "MAX_LEAVES_ITEMS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_SUMMARY_CHARS",
    "MAX_TITLE_CHARS",
    "MIN_ACCEPTANCE_ITEMS",
    "MIN_ATTEMPT",
    "PLANNABLE_ROLES",
    "Task",
    "TaskOutcome",
    "TaskSpec",
    "check_role_is_plannable",
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
    role: WorkerRole = Field(
        default=WorkerRole.DRONE,
        description="Which Worker role the Warden spawns for this task; one of PLANNABLE_ROLES.",
    )
    needs: TaskNeeds = Field(
        default_factory=TaskNeeds, description="What the task requires from its Cell."
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C1, description="The data-sensitivity label of the task itself."
    )
    origin: RequestOrigin = Field(
        default=RequestOrigin.HUMAN,
        description="Who asked for this task to exist (roadmap step 5.7a). Defaults to HUMAN, "
        "matching every task minted from a `hive run`/`hive tasks submit` file today; a planned "
        "sub-task inherits its goal's own origin (hivemind.queen.planner.plan._to_task_draft).",
    )
    depends_on: tuple[TaskIdField, ...] = Field(
        default=(),
        max_length=MAX_DEPENDENCIES,
        description="Ids of tasks that must SUCCEED before this one may start.",
    )
    leaves: tuple[PlannedLeaving, ...] = Field(
        default=(),
        max_length=MAX_LEAVES_ITEMS,
        description="What the plan declared should stay on this task's Cell once its lease is "
        "released (roadmap step 5.0b); empty unless the goal itself asks for something to "
        "remain. Carried unchanged into the task.assign this task's Warden sends.",
    )
    capabilities: GoalCapabilities = Field(
        default=None,
        description="The capability set of the goal this task was planned from (roadmap step "
        "10.3, ADR-0039), sorted capability strings: placement and the Warden never let the "
        "task do more. None means the operator's own local submission, with no device ceiling.",
    )
    # Roadmap step 10.5 (ADR-0040): the request this task's goal was planned from, and the budget
    # it set; both ride in the task's JSON body, so neither store needs a migration.
    goal_request_id: GoalRequestRef = None
    spend_cap_usd: GoalSpendCap = None

    @field_validator("depends_on")
    @classmethod
    def _depends_on_has_no_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a depends_on tuple that names the same task id more than once."""
        if len(set(value)) != len(value):
            raise ValueError(f"TaskSpec.depends_on contains a duplicate id: {value!r}.")
        return value

    @field_validator("role")
    @classmethod
    def _role_is_plannable(cls, value: WorkerRole) -> WorkerRole:
        """Reject a WorkerRole outside PLANNABLE_ROLES; see that constant's own comment."""
        check_role_is_plannable(value, owner="TaskSpec")
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
    scout_report: ScoutReport | None = Field(
        default=None,
        description="What a Scout found (roadmap 6.10); set only when role is SCOUT, copied "
        "unchanged from the Warden-verified TaskResult that closed the task.",
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
    bound_tier: CombShieldLevel | None = Field(
        default=None,
        description="The Comb Shield tier the task is bound to on its Cell (roadmap step "
        "10.3b): set at assignment from the Cell it lands on, carried to every later check for "
        "the task, and cleared when the task leaves the Cell. None whenever it is not placed.",
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


def check_role_is_plannable(role: WorkerRole, *, owner: str) -> None:
    """Reject a WorkerRole outside PLANNABLE_ROLES; shared by TaskSpec and TaskDraft.

    One helper, not two copies of the same message, so the two field_validators that call it (on
    TaskSpec.role and TaskDraft.role) can never drift on which roles they allow or how they say so.

    Args:
        role: The role a caller is about to accept.
        owner: The class name to name in the error ("TaskSpec" or "TaskDraft"), so the ladder-
            retryable ValueError points a caller at the field that actually rejected it.

    Raises:
        ValueError: `role` is not one of PLANNABLE_ROLES.
    """
    if role in PLANNABLE_ROLES:
        return
    allowed = ", ".join(sorted(member.value for member in PLANNABLE_ROLES))
    raise ValueError(f"{owner}.role must be one of {allowed}; got {role.value}.")


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
    """Require warden_id and cell_id together, set exactly when placed; no tier when not."""
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
    # A tier binding belongs to a placement: a task off every Cell is bound to none.
    if not placed and task.bound_tier is not None:
        raise ValueError(f"Task {task.id} is {task.status.name} and must not carry a bound tier.")


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
