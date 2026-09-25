"""Tests for hivemind.brood_chamber.chamber.base: ChamberIdentity and the Night Veil cut.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/base.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one). `_ChamberBase` itself (the store/clock/identity plumbing every mixin shares)
    is private, and most of its behaviour is what every test in this test package already
    exercises through BroodChamber's public methods (chamber/test_submission.py,
    test_lifecycle.py, test_outcomes.py, test_questions.py, test_queries.py); this file directly
    tests ChamberIdentity, the one public name base.py defines, and the one rule `_build_event`
    adds for every mixin: a Night Veil task's events carry nothing beyond its id (codingrules 12).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.base for the module under test.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from builders.tasks import make_answer, make_graph_draft, make_outcome

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.cell import CombShieldLevel, Isolation, TaskNeeds
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id, new_warden_id, new_worker_id

_NIGHT_VEIL_NEEDS = TaskNeeds(comb_shield=CombShieldLevel.NIGHT_VEIL, isolation=Isolation.REQUIRED)


def test_chamber_identity_holds_hive_node_and_actor() -> None:
    clock = FakeClock()
    hive_id, node_id = new_hive_id(clock), new_node_id(clock)

    identity = ChamberIdentity(hive_id=hive_id, node_id=node_id, actor="system")

    assert identity.hive_id == hive_id
    assert identity.node_id == node_id
    assert identity.actor == "system"


def test_chamber_identity_is_frozen() -> None:
    clock = FakeClock()
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )

    with pytest.raises(FrozenInstanceError):
        identity.actor = "human"  # type: ignore[misc]  # deliberately mutating a frozen dataclass


async def _submitted(needs: TaskNeeds) -> tuple[BroodChamber, MemoryPheromoneTrail, Task]:
    """A chamber over a memory store, and one task submitted with `needs`."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    chamber = BroodChamber(MemoryTaskStore(trail), clock, identity)
    draft = make_graph_draft({"haiku": ()})
    draft = draft.model_copy(
        update={"tasks": (draft.tasks[0].model_copy(update={"needs": needs}),)}
    )
    (task,) = await chamber.submit(draft)
    return chamber, trail, task


async def _run_to_success(chamber: BroodChamber, task: Task, tier: CombShieldLevel) -> None:
    """Walk `task` through every edge a question-asking run makes, then succeed it."""
    clock = FakeClock()
    await chamber.assign(
        task.id, new_warden_id(clock), new_cell_id(clock), "Placed.", bound_tier=tier
    )
    await chamber.start(task.id)
    await chamber.report_progress(task.id, "Half the haiku is written.", 0.5)
    question = await chamber.ask(task.id, new_worker_id(clock), "Which season?")
    await chamber.answer(question.id, make_answer())
    await chamber.complete(task.id, make_outcome())


async def test_a_night_veil_tasks_every_event_carries_nothing_beyond_its_id() -> None:
    chamber, trail, task = await _submitted(_NIGHT_VEIL_NEEDS)

    await _run_to_success(chamber, task, CombShieldLevel.NIGHT_VEIL)

    events = await trail.query(TrailQuery(subject_id=task.id))
    assert [event.kind for event in events] == [
        "task.submitted",
        "task.assigned",
        "task.started",
        "task.progressed",
        "task.blocked",
        "task.answered",
        "task.succeeded",
    ]
    assert all(event.payload == {} for event in events)
    # Only the trail is cut: the chamber's own row still holds what the human asked for.
    stored = await chamber.get(task.id)
    assert stored.spec.title == "Task haiku" and stored.status is TaskStatus.SUCCEEDED


async def test_a_night_veil_tasks_submission_never_records_its_title() -> None:
    _chamber, trail, task = await _submitted(_NIGHT_VEIL_NEEDS)

    [submitted] = await trail.query(TrailQuery(subject_id=task.id))

    assert "title" not in submitted.payload and submitted.payload == {}


async def test_a_withdrawn_questions_reason_is_cut_for_a_night_veil_task() -> None:
    chamber, trail, task = await _submitted(_NIGHT_VEIL_NEEDS)
    clock = FakeClock()
    await chamber.assign(
        task.id,
        new_warden_id(clock),
        new_cell_id(clock),
        "Placed.",
        bound_tier=task.spec.needs.comb_shield,
    )
    await chamber.start(task.id)
    question = await chamber.ask(task.id, new_worker_id(clock), "Which season?")

    await chamber.withdraw(question.id, "the Drone found the answer in its brief")

    [withdrawn] = await trail.query(TrailQuery(kind="task.question_withdrawn"))
    assert withdrawn.payload == {}


async def test_binding_to_a_night_veil_cell_cuts_the_assignment_whatever_was_asked() -> None:
    chamber, trail, task = await _submitted(TaskNeeds())
    clock = FakeClock()

    await chamber.assign(
        task.id,
        new_warden_id(clock),
        new_cell_id(clock),
        "Placed.",
        bound_tier=CombShieldLevel.NIGHT_VEIL,
    )

    [assigned] = await trail.query(TrailQuery(kind="task.assigned"))
    assert assigned.payload == {}


async def test_a_meadow_tasks_events_keep_their_payloads() -> None:
    chamber, trail, task = await _submitted(TaskNeeds())

    await _run_to_success(chamber, task, CombShieldLevel.MEADOW)

    by_kind = {event.kind: event for event in await trail.query(TrailQuery(subject_id=task.id))}
    assert by_kind["task.submitted"].payload["title"] == "Task haiku"
    assert by_kind["task.assigned"].payload["bound_tier"] == CombShieldLevel.MEADOW.value
    assert "question_id" in by_kind["task.blocked"].payload
