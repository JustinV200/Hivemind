"""Tests for pausing an isolated Cell's bees: the levers, the bounded wait, and the chamber.

Roadmap step 10.6a. Every task placed on the Cell is sent Clustering's checkpoint-and-pause pair;
the wait for each bee's answer (`worker.paused`, or its end) runs on the injected clock and never
past the bound; a bee's answer names only the bee, so its Warden's `worker.spawned` row maps it to
its task. A task BLOCKED on a Question has the Question withdrawn before it is paused. A Night
Veil Cell's bees answer into its segment alone (their Warden's shipped records never reach the
durable trail), and the wait reads that segment too, so their answer ends it at once.

Fits into the Hive:
    Mirrors src/hivemind/queen/isolation/pause.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.protocol for the same lever pair under Clustering.
"""

from __future__ import annotations

import asyncio

from builders.isolation import night_veil_cell, place_running, veil_cell
from builders.queen import make_queen_deps

from hivemind.brood_chamber import TaskStatus
from hivemind.pheromone import TrailQuery, TrailSegment, WorkerEvent
from hivemind.queen.deps import QueenDeps
from hivemind.queen.isolation.pause import POLL_INTERVAL_S, SPAWNED_KIND, pause_cell_bees
from waggle.clock import FakeClock
from waggle.ids import NodeId, TaskId, WardenId, WorkerId, new_event_id, new_node_id, new_worker_id

_BOUND_S = 1.0  # The wait's bound in these tests, in fake seconds.


async def _record_spawn(deps: QueenDeps, warden_id: WardenId, task_id: TaskId) -> WorkerId:
    """Record the Warden's `worker.spawned` for a fresh bee on `task_id`; return the bee."""
    bee = new_worker_id(deps.clock)
    await deps.trail.record(_spawned(deps, deps.identity.node_id, warden_id, task_id, bee))
    return bee


async def _record_paused(deps: QueenDeps, bee: WorkerId) -> None:
    """Record the bee's own `worker.paused`, as its runtime does on a TaskPause."""
    await deps.trail.record(_paused(deps, deps.identity.node_id, bee))


def _spawned(
    deps: QueenDeps, node: NodeId, warden_id: WardenId, task_id: TaskId, bee: WorkerId
) -> WorkerEvent:
    """The Warden's `worker.spawned` for `bee` on `task_id`, recorded on `node`."""
    return WorkerEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=node,
        at=deps.clock.now(),
        actor=warden_id,
        kind=SPAWNED_KIND,
        subject_id=bee,
        payload={"task_id": task_id, "role": "DRONE"},
    )


def _paused(deps: QueenDeps, node: NodeId, bee: WorkerId) -> WorkerEvent:
    """The bee's own `worker.paused`, recorded on `node`, as its runtime does on a TaskPause."""
    return WorkerEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=node,
        at=deps.clock.now(),
        actor=bee,
        kind="worker.paused",
        subject_id=bee,
        payload={},
    )


async def test_a_bee_that_answers_inside_the_bound_is_acknowledged() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock)
    task = await place_running(deps, link)
    bee = await _record_spawn(deps, link.warden_id, task.id)

    pausing = asyncio.ensure_future(pause_cell_bees(deps, link, "Isolated.", _BOUND_S))
    await warden_end.wait_for_task_pause()
    await _record_paused(deps, bee)
    clock.advance(POLL_INTERVAL_S)
    outcome = await asyncio.wait_for(pausing, timeout=5.0)

    assert outcome.paused == (task.id,) and outcome.unacknowledged == ()
    assert (await deps.chamber.get(task.id)).status is TaskStatus.PAUSED
    await warden_end.close()


async def test_the_wait_never_outlasts_its_bound() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock)
    task = await place_running(deps, link)

    pausing = asyncio.ensure_future(pause_cell_bees(deps, link, "Isolated.", _BOUND_S))
    await warden_end.wait_for_task_pause()
    for _ in range(int(_BOUND_S / POLL_INTERVAL_S) + 1):
        await asyncio.sleep(0)
        clock.advance(POLL_INTERVAL_S)
    outcome = await asyncio.wait_for(pausing, timeout=5.0)

    # Silent within the bound: still paused in the chamber, and named as unacknowledged.
    assert outcome.paused == (task.id,) and outcome.unacknowledged == (task.id,)
    await warden_end.close()


async def test_a_task_blocked_on_a_question_has_it_withdrawn_before_it_is_paused() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock)
    task = await place_running(deps, link)
    await deps.chamber.ask(task.id, new_worker_id(clock), "Which season?")

    outcome = await pause_cell_bees(deps, link, "Isolated.", 0.0)

    paused = await deps.chamber.get(task.id)
    assert outcome.paused == (task.id,)
    assert paused.status is TaskStatus.PAUSED and paused.pending_question_id is None
    await warden_end.close()


async def test_only_the_tasks_on_the_isolated_cell_are_touched() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock)
    other_deps, other_link, other_end = make_queen_deps(clock)
    elsewhere = await place_running(deps, other_link)  # Same chamber, another Warden's Cell.

    outcome = await pause_cell_bees(deps, link, "Isolated.", 0.0)

    assert outcome.paused == () and outcome.unacknowledged == ()
    assert (await deps.chamber.get(elsewhere.id)).status is TaskStatus.RUNNING
    del other_deps  # Only its link (another Cell) was needed.
    await warden_end.close()
    await other_end.close()


async def test_a_night_veil_bees_answer_in_its_cells_segment_ends_the_wait() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, cell=night_veil_cell(clock))
    deps, segments = veil_cell(deps, link.cell.id)
    task = await place_running(deps, link)
    node, bee = new_node_id(clock), new_worker_id(clock)  # The Cell's own node, and its bee.

    pausing = asyncio.ensure_future(pause_cell_bees(deps, link, "Isolated.", _BOUND_S))
    await warden_end.wait_for_task_pause()
    shipped = (_spawned(deps, node, link.warden_id, task.id, bee), _paused(deps, node, bee))
    # What its Warden ships lands in the Cell's segment alone, never on the durable trail.
    await segments.merge(
        link.cell.id, TrailSegment(node_id=node, exported_at=clock.now(), events=shipped)
    )
    clock.advance(POLL_INTERVAL_S)
    outcome = await asyncio.wait_for(pausing, timeout=5.0)

    assert await deps.trail.query(TrailQuery(kind="worker.paused")) == ()
    assert outcome.paused == (task.id,) and outcome.unacknowledged == ()
    await warden_end.close()
