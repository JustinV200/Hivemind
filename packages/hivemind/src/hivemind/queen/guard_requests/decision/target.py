"""Find what a Guard request is aimed at: its Cell, and the tasks it implicates on that Cell.

A Guard report (ADR-0043) names its target by ids: a Cell for `ISOLATE_CELL` or `STING_CUT`, bees
or tasks for `QUARANTINE_BEE`, and whichever of them the rule saw. The Queen's decision needs two
answers from those ids. `target_cell` is the Cell an isolation would cut off: the one the report
names, else the Cell the first of its tasks is placed on (the Brood Chamber records where every
placed task runs). `implicated_tasks` is what a quarantine would hold: the tasks the report names,
plus the task of every bee it names (the Warden's `worker.spawned` record says which task a bee
runs), each still placed, and on the target Cell when there is one.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests decision sub-package. Called by `.decide` and `.act`. Calls into
    `hivemind.brood_chamber`, `hivemind.guard` (GuardReport), `hivemind.pheromone` (TrailQuery)
    and waggle only; `QueenDeps` only for its type.

Key invariants:
    - Reads only: nothing here changes a task, a Cell or the trail.
    - Every answer is bounded by the report's own bounded id lists.

See Also:
    - hivemind.guard.report for what a report may name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task, TaskNotFoundError
from hivemind.guard import GuardReport
from hivemind.pheromone import TrailQuery
from waggle.ids import CellId, TaskId

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps

SPAWNED_KIND = "worker.spawned"  # The Warden's record of a sub-bee: its subject and its task.

__all__ = ["implicated_tasks", "target_cell"]


async def target_cell(deps: QueenDeps, report: GuardReport) -> CellId | None:
    """Return the Cell `report` is aimed at: the one it names, else its first placed task's.

    Args:
        deps: The Queen's collaborators; `chamber` says where a task runs.
        report: The Guard request's report.

    Returns:
        The Cell, or None when the report names none and none of its tasks is placed.
    """
    if report.cell_id is not None:
        return CellId(report.cell_id)
    for task in await _placed(deps, [TaskId(task_id) for task_id in report.task_ids]):
        return task.cell_id
    return None


async def implicated_tasks(
    deps: QueenDeps, report: GuardReport, cell_id: CellId | None
) -> tuple[Task, ...]:
    """Return the placed tasks `report` implicates, on `cell_id` when one is given.

    Args:
        deps: The Queen's collaborators.
        report: The Guard request's report.
        cell_id: The target Cell; None keeps every placed task wherever it runs.

    Returns:
        The tasks, the report's own first and then its bees', each once.
    """
    named = [TaskId(task_id) for task_id in report.task_ids]
    # A bee named without its task: its Warden's spawn record says which task it runs.
    for bee_id in report.bee_ids:
        query = TrailQuery(kind=SPAWNED_KIND, subject_id=bee_id, newest_first=True, limit=1)
        spawned = await deps.trail.query(query)
        if spawned and spawned[0].payload.get("task_id"):
            named.append(TaskId(str(spawned[0].payload["task_id"])))
    placed = await _placed(deps, list(dict.fromkeys(named)))
    return tuple(task for task in placed if cell_id is None or task.cell_id == cell_id)


async def _placed(deps: QueenDeps, task_ids: list[TaskId]) -> list[Task]:
    """Return the tasks among `task_ids` the chamber knows and has placed on a Cell, in order."""
    placed: list[Task] = []
    for task_id in task_ids:
        try:
            task = await deps.chamber.get(task_id)
        except TaskNotFoundError:
            continue  # A report may name a task from before a restart the chamber never held.
        if task.cell_id is not None:
            placed.append(task)
    return placed
