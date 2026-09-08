"""Contract suite for TaskStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.brood_chamber.store.TaskStore contract and runs against both implementations that
    ship: hivemind.brood_chamber.memory.MemoryTaskStore (over MemoryPheromoneTrail) and
    hivemind.brood_chamber.sqlite.SqliteTaskStore (over SqlitePheromoneTrail, both on the same
    tmp_path SQLite file). A new implementation joins the fixture's params and must pass here
    before it is used anywhere else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.store for the TaskStore protocol under test.
    - packages/hivemind/tests/contracts/test_pheromone_trail_contract.py for the pattern this
      mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.tasks import make_answer, make_question, make_task

from hivemind.brood_chamber.errors import (
    QuestionNotFoundError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from hivemind.brood_chamber.memory import MemoryTaskStore
from hivemind.brood_chamber.questions import Question, QuestionStatus
from hivemind.brood_chamber.sqlite import SqliteTaskStore
from hivemind.brood_chamber.store import TaskFilter, TaskStore
from hivemind.brood_chamber.task import Task
from hivemind.brood_chamber.task_state import TaskStatus
from hivemind.common.errors import ConflictError, InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.pheromone import DuplicateEventError, PheromoneTrail, TaskEvent
from hivemind.pheromone.memory import MemoryPheromoneTrail
from hivemind.pheromone.sqlite import SqlitePheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

_STORE_KINDS = ("memory", "sqlite")


def _make_task_event(clock: FakeClock, task: Task, kind: str = "task.submitted") -> TaskEvent:
    """Build a well-formed TaskEvent whose subject is `task`, minting a fresh id and node."""
    return TaskEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=task.id,
        payload={},
    )


@dataclass(frozen=True, slots=True)
class _StoreAndTrail:
    """A TaskStore and the PheromoneTrail it records events on, for the same fixture kind."""

    store: TaskStore
    trail: PheromoneTrail


@pytest.fixture(params=_STORE_KINDS)
async def store_and_trail(request: pytest.FixtureRequest, tmp_path: Path) -> _StoreAndTrail:
    """A (TaskStore, PheromoneTrail) pair of the parametrised kind, sharing one clock and file."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _StoreAndTrail(store=MemoryTaskStore(memory_trail), trail=memory_trail)
    # SQLite: two connections to the same file (ADR-0006 decision 1: two stores that write the
    # same file use separate connections); the trail's migration must run before the store's own.
    db_path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db_path), clock)
    sqlite_store = await SqliteTaskStore.create(connect(db_path), clock)
    return _StoreAndTrail(store=sqlite_store, trail=sqlite_trail)


# ──────────────────────────────────────────────────────────────────────────────
# insert_tasks
# ──────────────────────────────────────────────────────────────────────────────


async def test_insert_tasks_then_get_task_returns_an_equal_task(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    task = make_task(clock=clock)
    event = _make_task_event(clock, task)

    await store_and_trail.store.insert_tasks([task], [event])
    result = await store_and_trail.store.get_task(task.id)

    assert result == task


async def test_insert_tasks_records_one_submitted_event_per_task_on_the_trail(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    task_a = make_task(clock=clock)
    task_b = make_task(clock=clock)
    event_a = _make_task_event(clock, task_a)
    event_b = _make_task_event(clock, task_b)

    await store_and_trail.store.insert_tasks([task_a, task_b], [event_a, event_b])

    results_a = await store_and_trail.trail.query(TrailQuery(subject_id=task_a.id))
    results_b = await store_and_trail.trail.query(TrailQuery(subject_id=task_b.id))
    assert results_a == (event_a,)
    assert results_b == (event_b,)


async def test_insert_tasks_duplicate_id_leaves_the_fresh_task_and_trail_unchanged(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    existing = make_task(clock=clock)
    existing_event = _make_task_event(clock, existing)
    await store_and_trail.store.insert_tasks([existing], [existing_event])
    fresh = make_task(clock=clock)
    duplicate = make_task(clock=clock, id=existing.id, goal_id=existing.goal_id)
    fresh_event = _make_task_event(clock, fresh)
    duplicate_event = _make_task_event(clock, duplicate)

    with pytest.raises(TaskAlreadyExistsError):
        await store_and_trail.store.insert_tasks([fresh, duplicate], [fresh_event, duplicate_event])

    with pytest.raises(TaskNotFoundError):
        await store_and_trail.store.get_task(fresh.id)
    assert await store_and_trail.trail.query(TrailQuery(subject_id=fresh.id)) == ()
    assert await store_and_trail.trail.query(TrailQuery(subject_id=existing.id)) == (
        existing_event,
    )


async def test_insert_tasks_mismatched_lengths_raises_invariant_violation_error(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    task = make_task(clock=clock)

    with pytest.raises(InvariantViolationError):
        await store_and_trail.store.insert_tasks([task], [])

    with pytest.raises(TaskNotFoundError):
        await store_and_trail.store.get_task(task.id)


# ──────────────────────────────────────────────────────────────────────────────
# update_task
# ──────────────────────────────────────────────────────────────────────────────


async def test_update_task_persists_the_new_value_and_its_event(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    task = make_task(status=TaskStatus.PENDING, clock=clock)
    await store_and_trail.store.insert_tasks([task], [_make_task_event(clock, task)])
    updated = make_task(
        status=TaskStatus.ASSIGNED,
        clock=clock,
        id=task.id,
        goal_id=task.goal_id,
        created_at=task.created_at,
    )
    event = _make_task_event(clock, updated, kind="task.assigned")

    await store_and_trail.store.update_task(updated, event)

    assert await store_and_trail.store.get_task(task.id) == updated
    trail_events = await store_and_trail.trail.query(TrailQuery(subject_id=task.id))
    assert event in trail_events


async def test_update_task_of_unknown_task_raises_task_not_found_and_records_nothing(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    task = make_task(clock=clock)
    event = _make_task_event(clock, task)

    with pytest.raises(TaskNotFoundError):
        await store_and_trail.store.update_task(task, event)

    assert await store_and_trail.trail.query(TrailQuery(subject_id=task.id)) == ()


async def test_update_task_with_wrong_subject_event_raises_invariant_violation_and_writes_nothing(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    task = make_task(clock=clock)
    await store_and_trail.store.insert_tasks([task], [_make_task_event(clock, task)])
    other = make_task(clock=clock)
    mismatched_event = _make_task_event(clock, other)

    with pytest.raises(InvariantViolationError):
        await store_and_trail.store.update_task(task, mismatched_event)

    assert await store_and_trail.store.get_task(task.id) == task
    assert await store_and_trail.trail.query(TrailQuery(subject_id=other.id)) == ()


async def test_update_task_with_a_duplicate_event_id_leaves_the_store_unchanged(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    task = make_task(status=TaskStatus.PENDING, clock=clock)
    submitted = _make_task_event(clock, task)
    await store_and_trail.store.insert_tasks([task], [submitted])
    updated = make_task(
        status=TaskStatus.ASSIGNED,
        clock=clock,
        id=task.id,
        goal_id=task.goal_id,
        created_at=task.created_at,
    )
    # Reuses `submitted`'s own id: whichever store this is, the underlying trail already knows it.
    duplicate_event = TaskEvent(**{**submitted.model_dump(), "kind": "task.assigned"})

    with pytest.raises(DuplicateEventError):
        await store_and_trail.store.update_task(updated, duplicate_event)

    assert await store_and_trail.store.get_task(task.id) == task
    assert await store_and_trail.trail.query(TrailQuery(subject_id=task.id)) == (submitted,)


# ──────────────────────────────────────────────────────────────────────────────
# list_tasks
# ──────────────────────────────────────────────────────────────────────────────


async def test_list_tasks_orders_by_created_at_then_id(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    first = make_task(clock=clock)
    clock.advance(1)
    second = make_task(clock=clock)
    clock.advance(1)
    third = make_task(clock=clock)
    for task in (third, first, second):  # inserted out of order on purpose
        await store_and_trail.store.insert_tasks([task], [_make_task_event(clock, task)])

    results = await store_and_trail.store.list_tasks(TaskFilter())

    assert [task.id for task in results] == [first.id, second.id, third.id]


async def test_list_tasks_filters_by_status(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    pending = make_task(status=TaskStatus.PENDING, clock=clock)
    running = make_task(status=TaskStatus.RUNNING, clock=clock)
    for task in (pending, running):
        await store_and_trail.store.insert_tasks([task], [_make_task_event(clock, task)])

    results = await store_and_trail.store.list_tasks(TaskFilter(status=TaskStatus.RUNNING))

    assert [task.id for task in results] == [running.id]


async def test_list_tasks_filters_by_goal_id(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    goal = make_task(clock=clock)
    member = make_task(clock=clock, goal_id=goal.id)
    other_goal = make_task(clock=clock)
    for task in (goal, member, other_goal):
        await store_and_trail.store.insert_tasks([task], [_make_task_event(clock, task)])

    results = await store_and_trail.store.list_tasks(TaskFilter(goal_id=goal.id))

    assert {task.id for task in results} == {goal.id, member.id}


async def test_list_tasks_respects_limit(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    for _ in range(3):
        task = make_task(clock=clock)
        await store_and_trail.store.insert_tasks([task], [_make_task_event(clock, task)])
        clock.advance(1)

    results = await store_and_trail.store.list_tasks(TaskFilter(limit=2))

    assert len(results) == 2


# ──────────────────────────────────────────────────────────────────────────────
# insert_question / update_question / get_question / list_questions
# ──────────────────────────────────────────────────────────────────────────────


def _blocked_task(clock: FakeClock, running: Task, question: Question) -> Task:
    """Move `running` to BLOCKED on `question`, keeping its placement fields."""
    return running.model_copy(
        update={
            "status": TaskStatus.BLOCKED,
            "pending_question_id": question.id,
            "updated_at": clock.now(),
        }
    )


async def test_insert_question_moves_task_to_blocked_and_stores_both_atomically(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    running = make_task(status=TaskStatus.RUNNING, clock=clock)
    await store_and_trail.store.insert_tasks([running], [_make_task_event(clock, running)])
    question = make_question(clock=clock, task_id=running.id)
    blocked = _blocked_task(clock, running, question)
    event = _make_task_event(clock, blocked, kind="task.blocked")

    await store_and_trail.store.insert_question(blocked, question, event)

    assert await store_and_trail.store.get_task(running.id) == blocked
    assert await store_and_trail.store.get_question(question.id) == question
    assert event in await store_and_trail.trail.query(TrailQuery(subject_id=running.id))


async def test_insert_question_of_unknown_task_raises_task_not_found(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    running = make_task(status=TaskStatus.RUNNING, clock=clock)
    question = make_question(clock=clock, task_id=running.id)
    blocked = _blocked_task(clock, running, question)
    event = _make_task_event(clock, blocked, kind="task.blocked")

    with pytest.raises(TaskNotFoundError):
        await store_and_trail.store.insert_question(blocked, question, event)


async def test_insert_question_duplicate_id_raises_conflict_error(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    running = make_task(status=TaskStatus.RUNNING, clock=clock)
    await store_and_trail.store.insert_tasks([running], [_make_task_event(clock, running)])
    question = make_question(clock=clock, task_id=running.id)
    blocked = _blocked_task(clock, running, question)
    await store_and_trail.store.insert_question(
        blocked, question, _make_task_event(clock, blocked, kind="task.blocked")
    )
    # Reuse the same question id for a second, distinct RUNNING task.
    other_running = make_task(status=TaskStatus.RUNNING, clock=clock)
    await store_and_trail.store.insert_tasks(
        [other_running], [_make_task_event(clock, other_running)]
    )
    other_blocked = _blocked_task(clock, other_running, question)

    with pytest.raises(ConflictError):
        await store_and_trail.store.insert_question(
            other_blocked, question, _make_task_event(clock, other_blocked, kind="task.blocked")
        )

    assert await store_and_trail.store.get_task(other_running.id) == other_running


async def test_update_question_with_an_answer_moves_task_back_to_running(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    running = make_task(status=TaskStatus.RUNNING, clock=clock)
    await store_and_trail.store.insert_tasks([running], [_make_task_event(clock, running)])
    question = make_question(clock=clock, task_id=running.id)
    blocked = _blocked_task(clock, running, question)
    await store_and_trail.store.insert_question(
        blocked, question, _make_task_event(clock, blocked, kind="task.blocked")
    )
    answer = make_answer(clock=clock)
    answered_question = question.model_copy(
        update={"status": QuestionStatus.ANSWERED, "answer": answer}
    )
    resumed = blocked.model_copy(
        update={
            "status": TaskStatus.RUNNING,
            "pending_question_id": None,
            "updated_at": clock.now(),
        }
    )
    event = _make_task_event(clock, resumed, kind="task.answered")

    await store_and_trail.store.update_question(resumed, answered_question, event)

    assert await store_and_trail.store.get_task(running.id) == resumed
    assert await store_and_trail.store.get_question(question.id) == answered_question
    assert event in await store_and_trail.trail.query(TrailQuery(subject_id=running.id))


async def test_update_question_unknown_question_raises_question_not_found(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    running = make_task(status=TaskStatus.RUNNING, clock=clock)
    await store_and_trail.store.insert_tasks([running], [_make_task_event(clock, running)])
    question = make_question(
        clock=clock,
        task_id=running.id,
        status=QuestionStatus.ANSWERED,
        answer=make_answer(clock=clock),
    )
    event = _make_task_event(clock, running, kind="task.answered")

    with pytest.raises(QuestionNotFoundError):
        await store_and_trail.store.update_question(running, question, event)


async def test_update_question_unknown_task_raises_task_not_found(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    # A question naming a task this store has never seen: update_question must check the task
    # row first (both implementations do the task UPDATE/lookup before touching the question).
    never_inserted = make_task(status=TaskStatus.RUNNING, clock=clock)
    question = make_question(
        clock=clock,
        task_id=never_inserted.id,
        status=QuestionStatus.ANSWERED,
        answer=make_answer(clock=clock),
    )
    event = _make_task_event(clock, never_inserted, kind="task.answered")

    with pytest.raises(TaskNotFoundError):
        await store_and_trail.store.update_question(never_inserted, question, event)


async def test_get_question_unknown_id_raises_question_not_found(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    question = make_question(clock=clock)

    with pytest.raises(QuestionNotFoundError):
        await store_and_trail.store.get_question(question.id)


async def test_list_questions_orders_by_asked_at_then_id_and_filters(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    running_a = make_task(status=TaskStatus.RUNNING, clock=clock)
    running_b = make_task(status=TaskStatus.RUNNING, clock=clock)
    await store_and_trail.store.insert_tasks(
        [running_a, running_b],
        [_make_task_event(clock, running_a), _make_task_event(clock, running_b)],
    )
    question_1 = make_question(clock=clock, task_id=running_a.id)
    clock.advance(1)
    question_2 = make_question(clock=clock, task_id=running_b.id)
    blocked_a = _blocked_task(clock, running_a, question_1)
    blocked_b = _blocked_task(clock, running_b, question_2)
    await store_and_trail.store.insert_question(
        blocked_a, question_1, _make_task_event(clock, blocked_a, kind="task.blocked")
    )
    await store_and_trail.store.insert_question(
        blocked_b, question_2, _make_task_event(clock, blocked_b, kind="task.blocked")
    )

    every_question = await store_and_trail.store.list_questions(task_id=None, status=None)
    assert [q.id for q in every_question] == [question_1.id, question_2.id]

    only_a = await store_and_trail.store.list_questions(task_id=running_a.id, status=None)
    assert [q.id for q in only_a] == [question_1.id]

    only_asked = await store_and_trail.store.list_questions(
        task_id=None, status=QuestionStatus.ASKED
    )
    assert {q.id for q in only_asked} == {question_1.id, question_2.id}

    none_answered = await store_and_trail.store.list_questions(
        task_id=None, status=QuestionStatus.ANSWERED
    )
    assert none_answered == ()
