"""Tests for hivemind.brood_chamber.chamber.queries: next_ready, get, list and pending_questions.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/queries.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one). Split out of the former test_chamber.py, which exercised the whole
    BroodChamber facade in one file; this file keeps only the tests that exercise
    `_QueriesMixin`'s methods. The pending_questions test calls `chamber.ask` only to create a
    question to read back; it is not a test of `ask` itself (see chamber/test_questions.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.queries for the module under test.
    - .claude/roadmap.md phase 2 step 2.8 for the facade's contract.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.errors import TaskNotFoundError
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.store.protocol import TaskFilter
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_worker_id


def _make_chamber(clock: FakeClock) -> tuple[BroodChamber, MemoryTaskStore, MemoryPheromoneTrail]:
    """Build a BroodChamber over a fresh MemoryTaskStore/MemoryPheromoneTrail pair."""
    trail = MemoryPheromoneTrail(clock)
    store = MemoryTaskStore(trail)
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return BroodChamber(store, clock, identity), store, trail


async def _seed(store: MemoryTaskStore, clock: FakeClock, status: TaskStatus) -> Task:
    """Insert a valid Task built by make_task straight into `store`, bypassing the chamber.

    Lets a test start from any status (RUNNING, BLOCKED, ...) without replaying every earlier
    transition; the event kind here is never read by anything but the store's own bookkeeping.
    """
    task = make_task(status=status, clock=clock)
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


async def test_next_ready_returns_the_earliest_ready_task_by_created_at() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    earlier = await _seed(store, clock, status=TaskStatus.PENDING)
    clock.advance(1)
    later = await _seed(store, clock, status=TaskStatus.PENDING)

    ready = await chamber.next_ready()

    assert ready is not None
    assert ready.id == earlier.id
    assert later.id != ready.id


async def test_next_ready_returns_none_when_nothing_is_ready() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    await _seed(store, clock, status=TaskStatus.RUNNING)

    assert await chamber.next_ready() is None


async def test_get_returns_the_stored_task() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.PENDING)

    assert await chamber.get(task.id) == task


async def test_get_unknown_task_raises_task_not_found() -> None:
    clock = FakeClock()
    chamber, _store, _trail = _make_chamber(clock)

    with pytest.raises(TaskNotFoundError):
        await chamber.get(make_task(clock=clock).id)


async def test_list_filters_by_status() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    await _seed(store, clock, status=TaskStatus.PENDING)
    running = await _seed(store, clock, status=TaskStatus.RUNNING)

    running_only = await chamber.list(TaskFilter(status=TaskStatus.RUNNING))

    assert [t.id for t in running_only] == [running.id]


async def test_pending_questions_returns_only_asked_status() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    question = await chamber.ask(task.id, new_worker_id(clock), "which?")

    pending = await chamber.pending_questions()

    assert [q.id for q in pending] == [question.id]
