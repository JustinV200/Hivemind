"""Serve the tasks resource: a page of tasks by goal and status, one task, and its brief.

Every task the Queen decomposed a goal into lives in the Brood Chamber, which the Entrance reads
directly (ADR-0040). ``GET /v1/tasks`` pages the task list oldest first by ``(created_at, id)``,
optionally one goal's or one status's, with a keyset cursor (``after``, the last task of the
previous page, which ``next_after`` hands back), so a page never shifts as tasks are added.
``GET /v1/tasks/{task_id}`` is one task with how it ended. Both answer ``TaskView``, which holds no
word of the task (codingrules 6.1: its title, objective and criteria are written from the human's
goal, so they are C2), and need only ``observe``. ``GET /v1/tasks/{task_id}/brief`` answers those
words (``TaskBriefView``) and so also needs ``honey:clearance:c2``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into the Brood Chamber (reads only) and the view models.

Key invariants:
    - Reading never changes the chamber; the words of a task are answered only behind C2.

See Also:
    - hivemind.observation.views.tasks for the per-field C2 decision.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.observation import (
    TaskBriefView,
    TaskPage,
    TaskView,
    task_brief,
    task_view,
)
from waggle.messages.base import TaskIdField

OBSERVE = "observe"  # Reading where work stands is a read-only view.
DEFAULT_TASK_PAGE = 50  # Tasks a page holds when the caller does not say.
MAX_TASK_PAGE = 500  # Most tasks one page answers.

TaskIdPath = Annotated[TaskIdField, Path(description="The task's id.")]

__all__ = ["DEFAULT_TASK_PAGE", "MAX_TASK_PAGE", "ROUTES"]


async def list_tasks(
    services: Services,
    goal_id: Annotated[TaskIdField | None, Query(description="Only this goal's tasks.")] = None,
    status: Annotated[TaskStatus | None, Query(description="Only tasks in this status.")] = None,
    after: Annotated[
        TaskIdField | None, Query(description="The previous page's next_after: read on from it.")
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_TASK_PAGE, description="Most tasks to return.")
    ] = DEFAULT_TASK_PAGE,
) -> TaskPage:
    """Read one page of tasks, oldest first.

    Args:
        services: The Entrance's services (the Brood Chamber).
        goal_id: Only this goal's tasks.
        status: Only tasks in this status.
        after: The cursor: only tasks after this one.
        limit: Most tasks to return.

    Returns:
        The page, and the cursor for the next one (null when this is the last).
    """
    query = TaskFilter(goal_id=goal_id, status=status, after=after, limit=limit)
    # Latency: one local indexed read of the Brood Chamber.
    tasks = await services.hive.chamber.list(query)
    # A full page may have more behind it; a short one is the last.
    next_after = tasks[-1].id if len(tasks) == limit else None
    return TaskPage(tasks=[task_view(task) for task in tasks], next_after=next_after)


async def read_task(task_id: TaskIdPath, services: Services) -> TaskView:
    """Read one task and how it ended, without its words.

    Args:
        task_id: The task.
        services: The Entrance's services.

    Returns:
        Its view.

    Raises:
        TaskNotFoundError: No such task (answered 404).
    """
    # Latency: one local primary-key read of the Brood Chamber.
    return task_view(await services.hive.chamber.get(task_id))


async def read_brief(task_id: TaskIdPath, services: Services) -> TaskBriefView:
    """Read what a task was asked to do and what it reported, in its own words (C2).

    Args:
        task_id: The task.
        services: The Entrance's services.

    Returns:
        Its brief.

    Raises:
        TaskNotFoundError: No such task (answered 404).
    """
    # Latency: one local primary-key read of the Brood Chamber.
    return task_brief(await services.hive.chamber.get(task_id))


_TASK = "/v1/tasks/{task_id}"

ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/tasks",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=list_tasks,
        summary="Read a page of tasks, by goal and status, without their words.",
        response_model=TaskPage,
    ),
    RouteSpec(
        method="GET",
        path=_TASK,
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=read_task,
        summary="Read one task and how it ended, without its words.",
        response_model=TaskView,
    ),
    RouteSpec(
        method="GET",
        path=f"{_TASK}/brief",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE, c2=True),
        effect=RouteEffect.READ,
        endpoint=read_brief,
        summary="Read a task's own words: title, objective, criteria, summaries (C2).",
        response_model=TaskBriefView,
    ),
)
