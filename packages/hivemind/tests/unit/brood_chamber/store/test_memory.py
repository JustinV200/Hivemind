"""Tests for hivemind.brood_chamber.store.memory: MemoryTaskStore's own failure-atomicity behaviour.

MemoryTaskStore's ordinary behaviour is covered by tests/contracts/test_task_store_contract.py,
parametrised over it and SqliteTaskStore together. This module covers what is specific to the
in-memory adapter: that a trail record failure leaves both dicts exactly as they were before the
call, the "validate, record, then swap in" shape the module docstring documents.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/store/memory.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.store.memory for the module under test.
    - tests/contracts/test_task_store_contract.py for the shared TaskStore behaviour.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task

from hivemind.brood_chamber.errors import TaskNotFoundError
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import DuplicateEventError, TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import EventId, new_event_id, new_hive_id, new_node_id


def _make_task_event(clock: FakeClock, subject_id: str, event_id: EventId, kind: str) -> TaskEvent:
    """Build a well-formed TaskEvent with an explicit `event_id`, so a caller can collide it."""
    return TaskEvent(
        id=event_id,
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


async def test_insert_tasks_with_a_pre_recorded_event_id_leaves_the_store_empty() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = MemoryTaskStore(trail)
    task = make_task(clock=clock)
    event_id = new_event_id(clock)
    event = _make_task_event(clock, task.id, event_id, "task.submitted")
    # Records the same id directly on the trail first, bypassing the store, so the store's own
    # insert_tasks call is guaranteed to hit DuplicateEventError on this event.
    await trail.record(_make_task_event(clock, task.id, event_id, "task.progressed"))

    with pytest.raises(DuplicateEventError):
        await store.insert_tasks([task], [event])

    with pytest.raises(TaskNotFoundError):
        await store.get_task(task.id)


async def test_update_task_with_a_pre_recorded_event_id_leaves_the_task_unchanged() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = MemoryTaskStore(trail)
    task = make_task(status=TaskStatus.PENDING, clock=clock)
    await store.insert_tasks(
        [task], [_make_task_event(clock, task.id, new_event_id(clock), "task.submitted")]
    )
    updated = task.model_copy(update={"last_summary": "changed", "updated_at": clock.now()})
    event_id = new_event_id(clock)
    await trail.record(_make_task_event(clock, task.id, event_id, "task.progressed"))
    colliding_event = _make_task_event(clock, task.id, event_id, "task.progressed")

    with pytest.raises(DuplicateEventError):
        await store.update_task(updated, colliding_event)

    assert await store.get_task(task.id) == task
