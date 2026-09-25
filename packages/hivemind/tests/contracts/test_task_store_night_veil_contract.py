"""Contract suite for TaskStore.scrub_night_veil: a finished Night Veil task keeps no words.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Runs the `scrub_night_veil` clause of the
    hivemind.brood_chamber.store.protocol.TaskStore contract over both implementations that ship,
    as tests.contracts.test_task_store_contract does for the rest: a finished task the Night Veil
    Cell's segment named, or one that asked for the tier, is reduced to its ids, states and
    timestamps with every question it asked; a live one keeps its words (it may be retried), any
    other task is untouched, and reducing again changes nothing.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.store.scrub for the skeleton a reduced row keeps.
    - .claude/codingrules.md section 12 for the boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.tasks import make_answer, make_question, make_task, make_task_spec

from hivemind.brood_chamber import MemoryTaskStore, SqliteTaskStore, Task, TaskStatus, TaskStore
from hivemind.brood_chamber.questions import Question, QuestionStatus
from hivemind.brood_chamber.store import SCRUBBED_TEXT
from hivemind.cell import CombShieldLevel, Isolation, TaskNeeds
from hivemind.common.sqlite import connect
from hivemind.pheromone import MemoryPheromoneTrail, SqlitePheromoneTrail, TaskEvent
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

_NIGHT_VEIL_NEEDS = TaskNeeds(comb_shield=CombShieldLevel.NIGHT_VEIL, isolation=Isolation.REQUIRED)


@pytest.fixture(params=("memory", "sqlite"))
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> TaskStore:
    """A TaskStore of the parametrised kind; SQLite on one tmp_path file, trail first."""
    clock = FakeClock()
    if request.param == "memory":
        return MemoryTaskStore(MemoryPheromoneTrail(clock))
    db_path = tmp_path / "hive.sqlite3"
    await SqlitePheromoneTrail.create(connect(db_path), clock)
    return await SqliteTaskStore.create(connect(db_path), clock)


def _event(clock: FakeClock, task: Task, kind: str) -> TaskEvent:
    """A well-formed TaskEvent about `task`: what every store write records beside it."""
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


async def _insert(store: TaskStore, clock: FakeClock, *tasks: Task) -> None:
    """Insert `tasks`, each with its own `task.submitted`."""
    await store.insert_tasks(tasks, [_event(clock, task, "task.submitted") for task in tasks])


async def _answered(store: TaskStore, clock: FakeClock, task: Task) -> Question:
    """Record one answered question on `task`, as its Worker asked and the Queen answered."""
    question = make_question(
        clock,
        task_id=task.id,
        text="Which mirror should I use?",
        options=("the first", "the second"),
        status=QuestionStatus.ANSWERED,
        answer=make_answer(clock, text="The second, it is closer.", chosen_option=1),
    )
    await store.insert_question(task, question, _event(clock, task, "task.blocked"))
    return question


async def test_a_finished_night_veil_task_and_its_questions_keep_no_words(
    store: TaskStore,
) -> None:
    clock = FakeClock()
    named = make_task(TaskStatus.SUCCEEDED, clock, last_summary="Wrote the haiku.")
    asked_for_it = make_task(TaskStatus.FAILED, clock, spec=make_task_spec(needs=_NIGHT_VEIL_NEEDS))
    await _insert(store, clock, named, asked_for_it)
    question = await _answered(store, clock, named)

    changed = await store.scrub_night_veil(frozenset({named.id}))

    assert changed == 3  # Two tasks and one question.
    for before in (named, asked_for_it):
        after = await store.get_task(before.id)
        assert after.spec.title == after.spec.objective == SCRUBBED_TEXT
        assert [item.subject for item in after.spec.acceptance] == [SCRUBBED_TEXT]
        assert after.last_summary is None
        assert after.outcome is not None and after.outcome.summary == SCRUBBED_TEXT
        # Its ids, state, times and costs are its skeleton, and stay exactly as they were.
        kept = ("id", "goal_id", "status", "created_at", "updated_at", "attempt")
        assert {k: getattr(after, k) for k in kept} == {k: getattr(before, k) for k in kept}
        assert before.outcome is not None and after.outcome.spend_usd == before.outcome.spend_usd
    scrubbed = await store.get_question(question.id)
    assert scrubbed.text == SCRUBBED_TEXT
    assert scrubbed.options == (SCRUBBED_TEXT, SCRUBBED_TEXT)
    assert scrubbed.answer is not None and scrubbed.answer.text == SCRUBBED_TEXT
    assert scrubbed.answer.chosen_option == 1 and scrubbed.status is QuestionStatus.ANSWERED


async def test_a_live_night_veil_task_and_any_other_task_keep_their_words(
    store: TaskStore,
) -> None:
    clock = FakeClock()
    live = make_task(TaskStatus.RUNNING, clock, spec=make_task_spec(needs=_NIGHT_VEIL_NEEDS))
    meadow = make_task(TaskStatus.SUCCEEDED, clock)
    await _insert(store, clock, live, meadow)

    assert await store.scrub_night_veil(frozenset({live.id})) == 0

    assert await store.get_task(live.id) == live  # It may yet run again, on another Cell.
    assert await store.get_task(meadow.id) == meadow


async def test_reducing_again_changes_nothing(store: TaskStore) -> None:
    clock = FakeClock()
    finished = make_task(TaskStatus.CANCELLED, clock, spec=make_task_spec(needs=_NIGHT_VEIL_NEEDS))
    await _insert(store, clock, finished)

    assert await store.scrub_night_veil(frozenset()) == 1
    once = await store.get_task(finished.id)

    assert await store.scrub_night_veil(frozenset({finished.id})) == 0
    assert await store.get_task(finished.id) == once
