"""Tests for re-placing a task whose Cell refused its commands (ADR-0060).

A task placed without `needs.commands` on a Real Cell that may not run commands comes back FAILED
with `ResultCause.CELL_RUNS_NO_COMMANDS`; a retry there would be refused again. The Queen instead
moves it back to PENDING needing commands and places it again at once, or fails it with
placement's own reason when no Cell could ever run them.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ready/requeue.py (codingrules section 3), and covers
    `hivemind.queen.ticks.results.requeue_task`, its one caller, beside it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.ready.requeue for the code under test.
    - hivemind.brood_chamber.chamber.lifecycle for the `requeue_for_commands` edge it calls.
"""

from __future__ import annotations

from builders.cells import make_cell
from builders.queen import make_queen_deps, make_warden_link
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import AccessLevel, CellKind
from hivemind.forage import GrantState
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.ticks.results import requeue_task
from waggle.clock import FakeClock
from waggle.ids import TaskId, new_warden_id


def _full_link(deps: QueenDeps) -> WardenLink:
    """A second attached Real Cell, granted FULL, so commands run there."""
    cell = make_cell(CellKind.REAL, clock=deps.clock, access_level=AccessLevel.FULL)
    identity = deps.identity
    warden_id = new_warden_id(deps.clock)
    link, _end = make_warden_link(identity.hive_id, warden_id, identity.node_id, cell, deps.clock)
    return link


async def _running_on(deps: QueenDeps, link: WardenLink) -> TaskId:
    """Submit one task with no needs and run it on `link`'s Cell, the only one offered."""
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    await dispatch_ready(deps, (link,))
    assert (await deps.chamber.get(task.id)).cell_id == link.cell.id
    return task.id


async def test_a_refused_task_is_placed_again_where_commands_run() -> None:
    # The default attached Cell: Real, SCRATCH, processes not contained.
    deps, scratch, _end = make_queen_deps(FakeClock())
    task_id = await _running_on(deps, scratch)
    full = _full_link(deps)

    attempt = await requeue_task(deps, (scratch, full), task_id, attempt=2)

    task = await deps.chamber.get(task_id)
    assert attempt == task.attempt == 2
    assert task.status is TaskStatus.RUNNING
    assert task.cell_id == full.cell.id
    assert task.spec.needs.commands is True
    [requeued] = await deps.trail.query(TrailQuery(kind="task.unassigned"))
    assert requeued.payload["from_status"] == "RUNNING"
    # The grant it held on the refusing Cell went back to the pool; only the new one is live.
    live = [grant for grant in deps.ledger.live_grants() if grant.task_id == task_id]
    assert [grant.cell_id for grant in live] == [full.cell.id]
    assert all(grant.state is not GrantState.REVOKED for grant in live)


async def test_a_refused_task_no_cell_could_ever_take_fails_with_the_placement_reason() -> None:
    # Only the SCRATCH Hive Stand is attached and no Virtual backend exists: rule 4d is final.
    deps, scratch, _end = make_queen_deps(FakeClock())
    task_id = await _running_on(deps, scratch)

    attempt = await requeue_task(deps, (scratch,), task_id, attempt=2)

    task = await deps.chamber.get(task_id)
    assert attempt is None
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    assert "Its Cell refused the commands it runs" in task.outcome.summary
    assert "Virtual" in task.outcome.summary
    assert task.spec.needs.commands is False  # Never re-placed, so never widened.


async def test_a_task_that_moved_on_meanwhile_is_left_alone() -> None:
    # A late result for a task no longer RUNNING (here: never placed) re-places nothing.
    deps, scratch, _end = make_queen_deps(FakeClock())
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))

    attempt = await requeue_task(deps, (scratch,), task.id, attempt=2)

    after = await deps.chamber.get(task.id)
    assert attempt is None
    assert after.status is TaskStatus.PENDING
    assert after.spec.needs.commands is False
