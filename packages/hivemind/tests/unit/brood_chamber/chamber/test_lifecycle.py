"""Tests for hivemind.brood_chamber.chamber.lifecycle: placement and Clustering moves.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/lifecycle.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one). Split out of the former test_chamber.py, which exercised the whole
    BroodChamber facade in one file; this file keeps only the tests that exercise
    `_LifecycleMixin`'s methods.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.lifecycle for the module under test.
    - .claude/roadmap.md phase 2 step 2.8 for the facade's contract.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import PheromoneEvent, TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_hive_id, new_node_id, new_warden_id


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


async def _events_for(trail: MemoryPheromoneTrail, subject_id: str) -> tuple[PheromoneEvent, ...]:
    """Return every trail event recorded for `subject_id`, in trail order."""
    return await trail.query(TrailQuery(subject_id=subject_id))


# ──────────────────────────────────────────────────────────────────────────────
# assign / unassign / start
# ──────────────────────────────────────────────────────────────────────────────


async def test_assign_moves_pending_to_assigned_and_places_it() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.PENDING)
    warden_id, cell_id = new_warden_id(clock), new_cell_id(clock)

    assigned = await chamber.assign(task.id, warden_id, cell_id, reason="placement")

    assert assigned.status is TaskStatus.ASSIGNED
    assert (assigned.warden_id, assigned.cell_id) == (warden_id, cell_id)
    assert assigned.attempt == 1


async def test_assign_from_running_raises_invalid_transition() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)

    with pytest.raises(InvalidTransitionError):
        await chamber.assign(task.id, new_warden_id(clock), new_cell_id(clock), reason="x")


async def test_unassign_then_assign_again_bumps_attempt() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.ASSIGNED)

    unassigned = await chamber.unassign(task.id, reason="warden lost")
    assert unassigned.status is TaskStatus.PENDING
    assert (unassigned.warden_id, unassigned.cell_id) == (None, None)
    assert unassigned.attempt == 2

    reassigned = await chamber.assign(task.id, new_warden_id(clock), new_cell_id(clock), "retry")
    assert reassigned.attempt == 2


async def test_start_moves_assigned_to_running() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.ASSIGNED)

    started = await chamber.start(task.id)

    assert started.status is TaskStatus.RUNNING


# ──────────────────────────────────────────────────────────────────────────────
# report_progress
# ──────────────────────────────────────────────────────────────────────────────


async def test_report_progress_updates_summary_and_fraction_while_running() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)

    updated = await chamber.report_progress(task.id, "halfway there", fraction_done=0.5)

    assert updated.status is TaskStatus.RUNNING
    assert updated.last_summary == "halfway there"
    assert updated.fraction_done == 0.5


async def test_report_progress_payload_carries_only_the_summary_length() -> None:
    clock = FakeClock()
    chamber, store, trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    sensitive_summary = "a summary that must never ride the trail verbatim"

    await chamber.report_progress(task.id, sensitive_summary, fraction_done=0.1)

    events = await _events_for(trail, task.id)
    progressed = next(e for e in events if e.kind == "task.progressed")
    assert progressed.payload == {"fraction_done": 0.1, "summary_length": len(sensitive_summary)}


async def test_report_progress_from_assigned_raises_invalid_transition() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.ASSIGNED)

    with pytest.raises(InvalidTransitionError):
        await chamber.report_progress(task.id, "progress", fraction_done=0.1)


# ──────────────────────────────────────────────────────────────────────────────
# pause / resume
# ──────────────────────────────────────────────────────────────────────────────


async def test_pause_moves_running_to_paused() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)

    paused = await chamber.pause(task.id, reason="provider outage")

    assert paused.status is TaskStatus.PAUSED
    assert paused.warden_id == task.warden_id  # placement survives a pause


async def test_resume_moves_paused_to_running() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.PAUSED)

    resumed = await chamber.resume(task.id, reason="provider back")

    assert resumed.status is TaskStatus.RUNNING


async def test_pause_from_pending_raises_invalid_transition() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.PENDING)

    with pytest.raises(InvalidTransitionError):
        await chamber.pause(task.id, reason="x")
