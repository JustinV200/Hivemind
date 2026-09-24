"""Define the tasks resource's read models: a task without its words, a page, and its C2 brief.

A task's record mixes two kinds of field (ADR-0032's reads, codingrules 8.11's views). Its
identifiers, status, placement, tiers, counts, spend and times say where work stands and carry
nothing personal, so ``TaskView`` holds them under ``observe``. Everything written from the human's
goal is ``C2`` (codingrules 6.1: any personal detail is C2, and the planner writes a task's title,
objective and acceptance from the goal's own words): the title, the objective, the acceptance
criteria (their paths, commands and expected text), the progress summary, the outcome's summary,
its artifact paths and the leavings a plan declared. Those live only in ``TaskBriefView``, served
by its own route that also needs ``honey:clearance:c2``, so an observe-only device follows every
task's progress and learns none of its words. ``TaskPage`` pages the list by keyset: ``next_after``
is the cursor for the next page.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models.views``. Used by
    ``hivemind.entrance.routes.hive.tasks`` and the task-graph stream; published in the OpenAPI
    document. Calls into the Brood Chamber's task model and pydantic.

Key invariants:
    - ``TaskView`` carries no text written from a goal: no title, objective, criterion, summary,
      artifact path or leaving, only ids, enums, numbers and times.
    - ``TaskBriefView`` is only ever answered behind ``honey:clearance:c2``.

See Also:
    - hivemind.brood_chamber.task.model for the record these views are cut from.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import CombShieldLevel, HoneyClearance, Isolation, RequestOrigin
from waggle.messages import Postcondition
from waggle.messages.base import CellIdField, MessageIdField, TaskIdField, WardenIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = ["TaskBriefView", "TaskOutcomeView", "TaskPage", "TaskView", "task_brief", "task_view"]


class TaskOutcomeView(BaseModel):
    """How a finished task ended, without its summary or artifact paths (both C2)."""

    model_config = _CONFIG

    status: TaskStatus = Field(description="SUCCEEDED, FAILED or CANCELLED.")
    verified_by: WardenIdField | None = Field(
        description="The Warden that ran its acceptance checks; set exactly for SUCCEEDED."
    )
    spend_usd: float = Field(description="What its last attempt cost, in US dollars.")
    artifact_count: int = Field(description="How many artifacts it produced (paths: the brief).")


class TaskView(BaseModel):
    """One task as an observing device sees it: where it stands, never what it says (observe)."""

    model_config = _CONFIG

    id: TaskIdField = Field(description="The task's id.")
    goal_id: TaskIdField = Field(description="Its goal: the goal's first task's id.")
    goal_request_id: str | None = Field(
        description="The goal request its goal was planned from (goalreq_...); null for a goal "
        "the operator submitted at the Hive Stand."
    )
    status: TaskStatus = Field(
        description="PENDING, ASSIGNED, RUNNING, BLOCKED, PAUSED, SUCCEEDED, FAILED or CANCELLED."
    )
    attempt: int = Field(description="Which attempt this is; the first is 1.")
    clearance: HoneyClearance = Field(description="The task's data-sensitivity label.")
    origin: RequestOrigin = Field(description="Who asked for it to exist: HUMAN, QUEEN or WARDEN.")
    depends_on: list[TaskIdField] = Field(description="Tasks that must succeed before it starts.")
    requested_tier: CombShieldLevel = Field(description="The Comb Shield tier it asked for.")
    isolation: Isolation = Field(description="Its isolation demand (REQUIRED never lands on Real).")
    bound_tier: CombShieldLevel | None = Field(
        description="The tier it is bound to on its Cell; null while it is not placed."
    )
    warden_id: WardenIdField | None = Field(description="Its Warden; null while not placed.")
    cell_id: CellIdField | None = Field(description="Its Cell; null while not placed.")
    blocked_on: MessageIdField | None = Field(
        description="The question it waits on; set exactly while BLOCKED."
    )
    fraction_done: float | None = Field(description="The Worker's own progress estimate, 0..1.")
    spend_cap_usd: float | None = Field(description="Its goal request's own budget, if it set one.")
    created_at: datetime = Field(description="When it was submitted.")
    updated_at: datetime = Field(description="When its record last changed.")
    outcome: TaskOutcomeView | None = Field(description="How it ended; null until it has.")


class TaskPage(BaseModel):
    """One page of tasks, oldest first by (created_at, id)."""

    model_config = _CONFIG

    tasks: list[TaskView] = Field(description="The page's tasks.")
    next_after: TaskIdField | None = Field(
        description="Pass as ?after= for the next page; null when this page is the last."
    )


class TaskBriefView(BaseModel):
    """What a task was asked to do and what it reported, in its own words (C2)."""

    model_config = _CONFIG

    id: TaskIdField = Field(description="The task's id.")
    title: str = Field(description="Its one-line summary.")
    objective: str = Field(description="Its full brief, written from the goal.")
    acceptance: list[Postcondition] = Field(
        description="The criteria its Warden checks before it may succeed."
    )
    last_summary: str | None = Field(description="Its latest progress summary, if any.")
    outcome_summary: str | None = Field(description="How it ended, in prose; null until then.")
    artifacts: list[str] = Field(description="The paths or URLs it produced.")


def task_view(task: Task) -> TaskView:
    """Shape a stored task for an observing device, leaving every word of it behind.

    Args:
        task: The task as the Brood Chamber holds it.

    Returns:
        Its view.
    """
    spec, outcome = task.spec, task.outcome
    return TaskView(
        id=task.id,
        goal_id=task.goal_id,
        goal_request_id=spec.goal_request_id,
        status=task.status,
        attempt=task.attempt,
        clearance=spec.clearance,
        origin=spec.origin,
        depends_on=list(spec.depends_on),
        requested_tier=spec.needs.comb_shield,
        isolation=spec.needs.isolation,
        bound_tier=task.bound_tier,
        warden_id=task.warden_id,
        cell_id=task.cell_id,
        blocked_on=task.pending_question_id,
        fraction_done=task.fraction_done,
        spend_cap_usd=spec.spend_cap_usd,
        created_at=task.created_at,
        updated_at=task.updated_at,
        outcome=None
        if outcome is None
        else TaskOutcomeView(
            status=outcome.status,
            verified_by=outcome.verified_by,
            spend_usd=outcome.spend_usd,
            artifact_count=len(outcome.artifacts),
        ),
    )


def task_brief(task: Task) -> TaskBriefView:
    """Shape a stored task's words for a device cleared for C2.

    Args:
        task: The task as the Brood Chamber holds it.

    Returns:
        Its brief.
    """
    outcome = task.outcome
    return TaskBriefView(
        id=task.id,
        title=task.spec.title,
        objective=task.spec.objective,
        acceptance=list(task.spec.acceptance),
        last_summary=task.last_summary,
        outcome_summary=outcome.summary if outcome is not None else None,
        artifacts=list(outcome.artifacts) if outcome is not None else [],
    )
