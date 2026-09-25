"""Tests for hivemind.brood_chamber.chamber.night_veil: what the teardown purge asks the chamber.

`bound_to` names the live tasks placed on a Cell and their Wardens, whatever their status and
however many there are (past one page of the store), and nothing of another Cell's or of a
finished task's; `scrub_night_veil` hands the store's own scrub through (its contract suite holds
what a reduced row keeps); `end_night_veil` cancels every live task still placed on the ending
Cell, whatever its status, and reduces it with the finished ones, leaving another Cell's alone.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/night_veil.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.night_veil for the module under test.
    - tests.contracts.test_task_store_night_veil_contract for the scrub itself.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task

import hivemind.brood_chamber.chamber.night_veil as night_veil_module
from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.store import SCRUBBED_TEXT
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_event_id, new_hive_id, new_node_id


def _chamber(clock: FakeClock) -> tuple[BroodChamber, MemoryTaskStore]:
    """A BroodChamber over a fresh in-memory store."""
    store = MemoryTaskStore(MemoryPheromoneTrail(clock))
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return BroodChamber(store, clock, identity), store


async def _seed(store: MemoryTaskStore, clock: FakeClock, task: Task) -> Task:
    """Insert `task` straight into `store`, bypassing the chamber's transitions."""
    event = TaskEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="task.submitted",
        subject_id=task.id,
        payload={},
    )
    await store.insert_tasks([task], [event])
    return task


async def _placed(
    store: MemoryTaskStore, clock: FakeClock, status: TaskStatus, cell: CellId
) -> Task:
    """Seed a task in `status` placed on `cell`."""
    return await _seed(store, clock, make_task(status, clock, cell_id=cell))


async def test_bound_to_names_every_live_task_on_the_cell_and_its_warden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    chamber, store = _chamber(clock)
    cell, other = new_cell_id(clock), new_cell_id(clock)
    # One page holds a single task here, so every status is read across pages.
    monkeypatch.setattr(night_veil_module, "MAX_TASK_FILTER_LIMIT", 1)
    running = await _placed(store, clock, TaskStatus.RUNNING, cell)
    blocked = await _placed(store, clock, TaskStatus.BLOCKED, cell)
    also_running = await _placed(store, clock, TaskStatus.RUNNING, cell)
    await _placed(store, clock, TaskStatus.RUNNING, other)
    await _seed(store, clock, make_task(TaskStatus.SUCCEEDED, clock))  # Ended: names no Cell.

    members = await chamber.bound_to(cell)

    placed = (running, blocked, also_running)
    wardens = {str(task.warden_id) for task in placed if task.warden_id is not None}
    assert members == {task.id for task in placed} | wardens
    assert cell not in members


async def test_scrub_night_veil_reduces_through_the_store() -> None:
    clock = FakeClock()
    chamber, store = _chamber(clock)
    finished = await _seed(store, clock, make_task(TaskStatus.SUCCEEDED, clock))

    assert await chamber.scrub_night_veil(frozenset({finished.id})) == 1

    assert (await chamber.get(finished.id)).spec.title == SCRUBBED_TEXT


async def test_end_night_veil_cancels_the_cells_live_tasks_and_reduces_them() -> None:
    clock = FakeClock()
    chamber, store = _chamber(clock)
    cell, other = new_cell_id(clock), new_cell_id(clock)
    running = await _placed(store, clock, TaskStatus.RUNNING, cell)
    blocked = await _placed(store, clock, TaskStatus.BLOCKED, cell)
    elsewhere = await _placed(store, clock, TaskStatus.RUNNING, other)
    finished = await _seed(store, clock, make_task(TaskStatus.SUCCEEDED, clock))

    reduced = await chamber.end_night_veil(cell, frozenset({finished.id}))

    for task in (running, blocked):
        ended = await chamber.get(task.id)
        assert (ended.status, ended.cell_id) == (TaskStatus.CANCELLED, None)
        assert ended.spec.title == SCRUBBED_TEXT
    assert (await chamber.get(finished.id)).spec.title == SCRUBBED_TEXT
    assert reduced == 3  # The two cancelled and the one already finished; questions have none.
    assert await chamber.get(elsewhere.id) == elsewhere  # Another Cell's task is untouched.
