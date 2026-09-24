"""Tests for hivemind.cell.leavings.store_sqlite: SqliteLeavingsStore's create() and durability.

The LeavingsStore Protocol's full behavioural coverage is in
tests/contracts/test_leavings_store_contract.py, parametrised over this implementation and
InMemoryLeavingsStore together. This module covers what is specific to the SQLite adapter:
create's loud failure when the Pheromone Trail's table is missing, migration idempotence, and
restart durability.

Fits into the Hive:
    Mirrors src/hivemind/cell/leavings/store_sqlite.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.leavings.store_sqlite for the module under test.
    - tests/contracts/test_leavings_store_contract.py for the shared LeavingsStore behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cells import make_leaving

from hivemind.cell.leavings.store_sqlite import SqliteLeavingsStore
from hivemind.common.errors import MigrationError
from hivemind.common.sqlite import connect
from hivemind.pheromone import CellEvent
from hivemind.pheromone.trail.sqlite import SqlitePheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id


def _left_event(clock: FakeClock, cell_id: str) -> CellEvent:
    """Build a well-formed cell.left CellEvent naming `cell_id`."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="cell.left",
        subject_id=cell_id,
        payload={},
    )


async def test_create_refuses_a_database_without_the_pheromone_table(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")  # no pheromone migration applied here

    with pytest.raises(MigrationError):
        await SqliteLeavingsStore.create(connection, clock)


async def test_create_applies_the_migration_and_is_idempotent(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    await SqlitePheromoneTrail.create(connection, clock)  # this store depends on this table

    first = await SqliteLeavingsStore.create(connection, clock)
    second = await SqliteLeavingsStore.create(connection, clock)

    assert isinstance(first, SqliteLeavingsStore)
    assert isinstance(second, SqliteLeavingsStore)
    rows = connection.execute(
        "SELECT version FROM schema_migrations WHERE subsystem = 'cell_leavings'"
    ).fetchall()
    assert [row["version"] for row in rows] == [1]


async def test_a_leaving_survives_closing_and_reopening_the_same_file(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    trail_connection = connect(db_path)
    await SqlitePheromoneTrail.create(trail_connection, clock)
    store_connection = connect(db_path)
    store = await SqliteLeavingsStore.create(store_connection, clock)
    leaving = make_leaving(clock=clock)
    await store.record_leaving(leaving, _left_event(clock, leaving.cell_id))
    store_connection.close()
    trail_connection.close()

    reopened_connection = connect(db_path)
    reopened_store = await SqliteLeavingsStore.create(reopened_connection, clock)
    result = await reopened_store.get_leaving(leaving.cell_id, leaving.path)

    assert result == leaving
