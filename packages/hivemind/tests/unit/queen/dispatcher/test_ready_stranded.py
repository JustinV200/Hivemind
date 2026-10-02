"""Tests for the dispatch pass's recovery of a task stranded ASSIGNED.

A task is ASSIGNED only between `chamber.assign` and `chamber.start` inside one dispatch pass, and
nothing reaches its Warden before `start`. A Queen that stopped (or a pass that failed) between
the two left the task ASSIGNED for good, and its goal never finished: the Brood Chamber's
ASSIGNED -> PENDING edge (`unassign`) existed but nothing in production called it (phase-7
handoff, section 8 item 5). Every pass now gives such a task back to PENDING first, so the same
pass places it afresh as a new attempt.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ready/dispatch.py (codingrules section 3), its
    stranded-task recovery only; the rest of the pass is covered beside it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.ready.dispatch for the code under test.
    - hivemind.brood_chamber.chamber.lifecycle for the `unassign` edge it calls.
"""

from __future__ import annotations

from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskStatus
from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.dispatcher.ready.dispatch import STRANDED_REASON
from waggle.clock import FakeClock


async def test_a_task_stranded_assigned_is_unassigned_and_placed_afresh() -> None:
    deps, link, end = make_queen_deps(FakeClock())
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    # As a Queen that stopped between assign and start leaves it: ASSIGNED, its Warden told nothing.
    await deps.chamber.assign(
        task.id, link.warden_id, link.cell.id, "Placed.", bound_tier=link.cell.comb_shield
    )

    await dispatch_ready(deps, (link,))

    placed = await deps.chamber.get(task.id)
    assert placed.status is TaskStatus.RUNNING
    assert placed.attempt == task.attempt + 1  # A new attempt, not the stranded one resumed.
    [unassigned] = await deps.trail.query(TrailQuery(kind="task.unassigned"))
    assert unassigned.subject_id == task.id
    assert unassigned.payload["reason"] == STRANDED_REASON
    assert (await end.wait_for_assignment()).task_id == task.id


async def test_a_pass_with_nothing_stranded_unassigns_nothing() -> None:
    deps, link, _end = make_queen_deps(FakeClock())
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))

    await dispatch_ready(deps, (link,))

    assert (await deps.chamber.get(task.id)).status is TaskStatus.RUNNING
    assert await deps.trail.query(TrailQuery(kind="task.unassigned")) == ()
