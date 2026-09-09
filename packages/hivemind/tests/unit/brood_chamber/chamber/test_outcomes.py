"""Tests for hivemind.brood_chamber.chamber.outcomes: complete, fail and cancel.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/outcomes.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one). Split out of the former test_chamber.py, which exercised the whole
    BroodChamber facade in one file; this file keeps only the tests that exercise
    `_OutcomesMixin`'s methods. The blocked-cancel test calls `chamber.ask` only to reach BLOCKED
    before cancelling; it is not a test of `ask` itself (see chamber/test_questions.py for those).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.outcomes for the module under test.
    - .claude/roadmap.md phase 2 step 2.8 for the facade's contract.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.model import Task, TaskOutcome
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone import TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_warden_id, new_worker_id


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


async def test_complete_moves_running_to_succeeded_and_clears_placement() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    outcome = TaskOutcome(
        status=TaskStatus.SUCCEEDED, summary="done", verified_by=new_warden_id(clock)
    )

    succeeded = await chamber.complete(task.id, outcome)

    assert succeeded.status is TaskStatus.SUCCEEDED
    assert succeeded.outcome == outcome
    assert (succeeded.warden_id, succeeded.cell_id) == (None, None)


async def test_complete_with_mismatched_outcome_status_raises_invariant_violation() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    wrong_outcome = TaskOutcome(status=TaskStatus.FAILED, summary="oops")

    with pytest.raises(InvariantViolationError):
        await chamber.complete(task.id, wrong_outcome)


async def test_fail_moves_running_to_failed() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    outcome = TaskOutcome(status=TaskStatus.FAILED, summary="gave up")

    failed = await chamber.fail(task.id, outcome)

    assert failed.status is TaskStatus.FAILED
    assert (failed.warden_id, failed.cell_id) == (None, None)


async def test_fail_with_mismatched_outcome_status_raises_invariant_violation() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    wrong_outcome = TaskOutcome(
        status=TaskStatus.SUCCEEDED, summary="oops", verified_by=new_warden_id(clock)
    )

    with pytest.raises(InvariantViolationError):
        await chamber.fail(task.id, wrong_outcome)


async def test_cancel_moves_pending_to_cancelled() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.PENDING)

    cancelled = await chamber.cancel(task.id, reason="no longer needed")

    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.outcome is not None
    assert cancelled.outcome.summary == "no longer needed"


async def test_cancel_moves_blocked_to_cancelled_and_clears_pending_question() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    await chamber.ask(task.id, new_worker_id(clock), "which?")

    cancelled = await chamber.cancel(task.id, reason="abandoned")

    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.pending_question_id is None
    assert (cancelled.warden_id, cancelled.cell_id) == (None, None)


async def test_cancel_a_terminal_task_raises_invalid_transition() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.SUCCEEDED)

    with pytest.raises(InvalidTransitionError):
        await chamber.cancel(task.id, reason="too late")
