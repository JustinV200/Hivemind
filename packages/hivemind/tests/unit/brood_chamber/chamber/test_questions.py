"""Tests for hivemind.brood_chamber.chamber.questions: ask, answer and withdraw.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/questions.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one). Split out of the former test_chamber.py, which exercised the whole
    BroodChamber facade in one file; this file keeps only the tests that exercise
    `_QuestionsMixin`'s methods.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.questions for the module under test.
    - .claude/roadmap.md phase 2 step 2.8 for the facade's contract.
"""

from __future__ import annotations

import json

import pytest
from builders.tasks import make_answer, make_task

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.questions import QuestionStatus
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import PheromoneEvent, TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
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


async def _events_for(trail: MemoryPheromoneTrail, subject_id: str) -> tuple[PheromoneEvent, ...]:
    """Return every trail event recorded for `subject_id`, in trail order."""
    return await trail.query(TrailQuery(subject_id=subject_id))


async def test_ask_moves_running_to_blocked_and_raises_a_question() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    asked_by = new_worker_id(clock)

    question = await chamber.ask(task.id, asked_by, "which option?", options=("a", "b"))

    assert question.status is QuestionStatus.ASKED
    assert question.asked_by == asked_by
    blocked = await chamber.get(task.id)
    assert blocked.status is TaskStatus.BLOCKED
    assert blocked.pending_question_id == question.id


async def test_ask_payload_never_carries_the_question_text() -> None:
    clock = FakeClock()
    chamber, store, trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    sensitive_question = "a very specific question that must not leak verbatim"

    question = await chamber.ask(task.id, new_worker_id(clock), sensitive_question)

    events = await _events_for(trail, task.id)
    blocked_event = next(e for e in events if e.kind == "task.blocked")
    assert set(blocked_event.payload) == {"question_id", "asked_by"}
    assert blocked_event.payload["question_id"] == question.id
    assert sensitive_question not in json.dumps(blocked_event.payload)


async def test_ask_from_pending_raises_invalid_transition() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.PENDING)

    with pytest.raises(InvalidTransitionError):
        await chamber.ask(task.id, new_worker_id(clock), "text?")


async def test_answer_moves_blocked_task_back_to_running() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    question = await chamber.ask(task.id, new_worker_id(clock), "which?")

    answered = await chamber.answer(question.id, make_answer(clock=clock))

    assert answered.status is TaskStatus.RUNNING
    assert answered.pending_question_id is None
    stored_question = await chamber.pending_questions(task.id)
    assert stored_question == ()


async def test_answering_an_already_answered_question_raises_invalid_transition() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    question = await chamber.ask(task.id, new_worker_id(clock), "which?")
    await chamber.answer(question.id, make_answer(clock=clock))

    with pytest.raises(InvalidTransitionError):
        await chamber.answer(question.id, make_answer(clock=clock))


async def test_withdraw_moves_blocked_task_back_to_running() -> None:
    clock = FakeClock()
    chamber, store, _trail = _make_chamber(clock)
    task = await _seed(store, clock, status=TaskStatus.RUNNING)
    question = await chamber.ask(task.id, new_worker_id(clock), "which?")

    withdrawn = await chamber.withdraw(question.id, reason="no longer needed")

    assert withdrawn.status is TaskStatus.RUNNING
    assert withdrawn.pending_question_id is None
