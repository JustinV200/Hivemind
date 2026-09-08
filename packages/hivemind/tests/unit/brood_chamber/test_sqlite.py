"""Tests for hivemind.brood_chamber.sqlite: SqliteTaskStore, create, and restart durability.

The eight TaskStore Protocol methods get their full behavioural coverage from
tests/contracts/test_task_store_contract.py, parametrised over this implementation and
MemoryTaskStore together. This module covers what is specific to the SQLite adapter: create's
loud failure when the Pheromone Trail's table is missing, migration idempotence, restart
durability, and that a trail insert failing inside a mutation's transaction rolls back the task
row alongside it.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/sqlite.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.sqlite for the module under test.
    - tests/contracts/test_task_store_contract.py for the shared TaskStore behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.tasks import make_task

from hivemind.brood_chamber.errors import TaskNotFoundError
from hivemind.brood_chamber.sqlite import SqliteTaskStore
from hivemind.brood_chamber.store import TaskFilter
from hivemind.brood_chamber.task_state import TaskStatus
from hivemind.common.errors import MigrationError
from hivemind.common.sqlite import connect
from hivemind.pheromone import DuplicateEventError, TaskEvent
from hivemind.pheromone.sqlite import SqlitePheromoneTrail
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


# ──────────────────────────────────────────────────────────────────────────────
# create()
# ──────────────────────────────────────────────────────────────────────────────


async def test_create_refuses_a_database_without_the_pheromone_table(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")  # no pheromone migration applied here

    with pytest.raises(MigrationError):
        await SqliteTaskStore.create(connection, clock)


async def test_create_applies_the_migration_and_is_idempotent(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    await SqlitePheromoneTrail.create(connection, clock)  # brood_chamber depends on this table

    first = await SqliteTaskStore.create(connection, clock)
    second = await SqliteTaskStore.create(connection, clock)

    assert isinstance(first, SqliteTaskStore)
    assert isinstance(second, SqliteTaskStore)
    rows = connection.execute(
        "SELECT version FROM schema_migrations WHERE subsystem = 'brood_chamber'"
    ).fetchall()
    assert [row["version"] for row in rows] == [1]


# ──────────────────────────────────────────────────────────────────────────────
# Restart durability
# ──────────────────────────────────────────────────────────────────────────────


async def test_tasks_and_questions_survive_closing_and_reopening_the_same_file(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    trail_connection = connect(db_path)
    await SqlitePheromoneTrail.create(trail_connection, clock)
    store_connection = connect(db_path)
    store = await SqliteTaskStore.create(store_connection, clock)
    task = make_task(status=TaskStatus.RUNNING, clock=clock)
    await store.insert_tasks(
        [task], [_make_task_event(clock, task.id, new_event_id(clock), "task.submitted")]
    )
    store_connection.close()
    trail_connection.close()

    reopened_connection = connect(db_path)
    reopened_store = await SqliteTaskStore.create(reopened_connection, clock)
    result = await reopened_store.get_task(task.id)

    assert result == task


# ──────────────────────────────────────────────────────────────────────────────
# Same-transaction rollback (Appendix C rule 3)
# ──────────────────────────────────────────────────────────────────────────────


async def test_insert_tasks_rolls_back_the_task_row_when_the_event_insert_fails(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    trail_connection = connect(db_path)
    trail = await SqlitePheromoneTrail.create(trail_connection, clock)
    store_connection = connect(db_path)
    store = await SqliteTaskStore.create(store_connection, clock)
    task = make_task(clock=clock)
    event_id = new_event_id(clock)
    # Records the same event id directly on the trail first (a different connection to the same
    # file), so the store's own insert_event call inside its transaction is guaranteed to fail.
    await trail.record(_make_task_event(clock, task.id, event_id, "task.progressed"))
    colliding_event = _make_task_event(clock, task.id, event_id, "task.submitted")

    with pytest.raises(DuplicateEventError):
        await store.insert_tasks([task], [colliding_event])

    with pytest.raises(TaskNotFoundError):
        await store.get_task(task.id)
    assert await store.list_tasks(TaskFilter()) == ()
