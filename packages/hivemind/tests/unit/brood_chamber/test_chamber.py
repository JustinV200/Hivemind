"""Tests for hivemind.brood_chamber.chamber: BroodChamber, the Queen's facade over the store.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/ (codingrules section 3: tests/unit mirrors src/
    one-to-one); the package's internal mixin split (base/submission/lifecycle/outcomes/
    questions/queries) is an implementation detail with no behaviour of its own outside
    BroodChamber, so this one file exercises the whole facade's public methods.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber for the package under test.
    - .claude/roadmap.md phase 2 step 2.8 for the facade's contract.
"""

from __future__ import annotations

import json

import pytest
from builders.tasks import make_answer, make_graph_draft, make_task

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.errors import InvalidTransitionError, TaskNotFoundError
from hivemind.brood_chamber.memory import MemoryTaskStore
from hivemind.brood_chamber.questions import QuestionStatus
from hivemind.brood_chamber.store import TaskFilter
from hivemind.brood_chamber.task import Task, TaskOutcome
from hivemind.brood_chamber.task_state import TaskStatus
from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone import PheromoneEvent, TaskEvent
from hivemind.pheromone.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import (
    new_cell_id,
    new_event_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
    new_worker_id,
)


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
# submit
# ──────────────────────────────────────────────────────────────────────────────


async def test_submit_mints_tasks_in_graph_order_sharing_one_goal_id() -> None:
    clock = FakeClock()
    chamber, _store, _trail = _make_chamber(clock)
    draft = make_graph_draft({"plan": (), "build": ("plan",)})

    tasks = await chamber.submit(draft)

    assert [t.status for t in tasks] == [TaskStatus.PENDING, TaskStatus.PENDING]
    assert tasks[0].goal_id == tasks[0].id
    assert tasks[1].goal_id == tasks[0].id
    assert tasks[1].spec.depends_on == (tasks[0].id,)


async def test_submit_records_one_task_submitted_event_per_task_without_the_objective() -> None:
    clock = FakeClock()
    chamber, _store, trail = _make_chamber(clock)
    sensitive_objective = "do not leak this objective text onto the trail"
    draft = make_graph_draft({"plan": ()})
    draft = draft.model_copy(
        update={"tasks": (draft.tasks[0].model_copy(update={"objective": sensitive_objective}),)}
    )

    (task,) = await chamber.submit(draft)

    events = await _events_for(trail, task.id)
    assert [e.kind for e in events] == ["task.submitted"]
    assert set(events[0].payload) == {"title", "goal_id", "depends_on"}
    assert sensitive_objective not in json.dumps(events[0].payload)


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
# ask / answer / withdraw
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# complete / fail / cancel
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# reads: next_ready / get / list / pending_questions
# ──────────────────────────────────────────────────────────────────────────────


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
