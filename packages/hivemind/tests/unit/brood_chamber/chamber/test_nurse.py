"""Tests for hivemind.brood_chamber.chamber.nurse: counting a task's Nurse Bee rounds.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/nurse.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.nurse for the module under test.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id


async def _seeded(
    status: TaskStatus,
) -> tuple[BroodChamber, MemoryPheromoneTrail, Task]:
    """A chamber over fresh memory stores, holding one task already at `status`."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = MemoryTaskStore(trail)
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    task = make_task(status=status, clock=clock)
    event = TaskEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind="task.submitted",
        subject_id=task.id,
        payload={},
    )
    await store.insert_tasks([task], [event])
    return BroodChamber(store, clock, identity), trail, task


async def test_note_nurse_round_counts_up_on_a_running_task_and_records_the_count() -> None:
    chamber, trail, task = await _seeded(TaskStatus.RUNNING)

    await chamber.note_nurse_round(task.id)
    updated = await chamber.note_nurse_round(task.id)

    assert updated.status is TaskStatus.RUNNING and updated.nurse_rounds == 2
    events = await trail.query(TrailQuery(subject_id=task.id, kind="task.nurse_round"))
    assert [event.payload for event in events] == [{"nurse_rounds": 1}, {"nurse_rounds": 2}]


async def test_note_nurse_round_refuses_a_task_that_is_not_running() -> None:
    chamber, _trail, task = await _seeded(TaskStatus.ASSIGNED)

    with pytest.raises(InvalidTransitionError):
        await chamber.note_nurse_round(task.id)
