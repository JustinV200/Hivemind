"""Tests for hivemind.queen.forage.ledger.store_sqlite: SqliteLedgerStore and its migration.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/store_sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.store_sqlite for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.forage import make_capacity, make_grant, make_reserve
from pydantic import ValidationError

from hivemind.common.sqlite import connect
from hivemind.forage.grant_state import GrantState
from hivemind.queen.forage.ledger.store_sqlite import SqliteLedgerStore
from waggle.clock import FakeClock
from waggle.ids import new_cell_id


async def test_create_applies_the_migration_and_is_idempotent(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "ledger.sqlite3")

    first = await SqliteLedgerStore.create(connection, clock)
    second = await SqliteLedgerStore.create(connection, clock)

    assert await first.list_grants() == ()
    assert await second.list_grants() == ()


async def test_put_grant_upserts_and_survives_reopening_the_same_file(tmp_path: Path) -> None:
    clock = FakeClock()
    db_path = tmp_path / "ledger.sqlite3"
    connection = connect(db_path)
    store = await SqliteLedgerStore.create(connection, clock)
    grant = make_grant(clock=clock, state=GrantState.ACTIVE)

    await store.put_grant(grant)
    connection.close()

    reopened = await SqliteLedgerStore.create(connect(db_path), clock)
    assert await reopened.list_grants() == (grant,)

    grown = grant.model_copy(update={"max_sub_bees": grant.max_sub_bees + 1})
    await reopened.put_grant(grown)
    rows = await reopened.list_grants()
    assert rows == (grown,)  # The same grant_id replaced the row, never appended a second one.


async def test_delete_grant_is_idempotent(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "ledger.sqlite3")
    store = await SqliteLedgerStore.create(connection, clock)
    grant = make_grant(clock=clock, state=GrantState.ACTIVE)
    await store.put_grant(grant)

    await store.delete_grant(grant.id)
    await store.delete_grant(grant.id)  # A second delete of an unknown id is a no-op.

    assert await store.list_grants() == ()


async def test_put_capacity_upserts_by_cell_id(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "ledger.sqlite3")
    store = await SqliteLedgerStore.create(connection, clock)
    cell_id = new_cell_id(clock)

    await store.put_capacity(cell_id, make_capacity(max_sub_bees=1))
    await store.put_capacity(cell_id, make_capacity(max_sub_bees=9))

    rows = await store.list_capacities()
    assert rows == ((cell_id, make_capacity(max_sub_bees=9)),)


async def test_reserve_round_trips_and_upserts(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "ledger.sqlite3")
    store = await SqliteLedgerStore.create(connection, clock)

    assert await store.get_reserve() is None

    await store.put_reserve(make_reserve(seats=3))
    await store.put_reserve(make_reserve(seats=8))

    restored = await store.get_reserve()
    assert restored is not None
    assert restored.seats == 8


async def test_migration_0003_creates_the_measured_grades_table(tmp_path: Path) -> None:
    connection = connect(tmp_path / "ledger.sqlite3")

    await SqliteLedgerStore.create(connection, FakeClock())

    columns = connection.execute("PRAGMA table_info(forage_ledger_measured_grades)").fetchall()
    assert [column["name"] for column in columns] == [
        "source_id",
        "grade",
        "measured_at",
        "evidence",
    ]


async def test_measured_grades_upsert_and_survive_reopening_the_same_file(tmp_path: Path) -> None:
    clock = FakeClock()
    db_path = tmp_path / "ledger.sqlite3"
    store = await SqliteLedgerStore.create(connect(db_path), clock)
    measured_at = clock.now()

    await store.put_measured_grade("fast-local", 2, measured_at, "first run")
    await store.put_measured_grade("fast-local", 4, measured_at, "second run")

    reopened = await SqliteLedgerStore.create(connect(db_path), clock)
    rows = await reopened.list_measured_grades()
    assert len(rows) == 1  # The same source_id replaced the row, never appended a second one.
    assert (rows[0].source_id, rows[0].grade, rows[0].evidence) == ("fast-local", 4, "second run")
    assert rows[0].measured_at == measured_at


async def test_measured_grades_list_is_empty_for_a_fresh_store(tmp_path: Path) -> None:
    store = await SqliteLedgerStore.create(connect(tmp_path / "ledger.sqlite3"), FakeClock())

    assert await store.list_measured_grades() == ()


async def test_put_measured_grade_refuses_a_grade_outside_one_to_five(tmp_path: Path) -> None:
    store = await SqliteLedgerStore.create(connect(tmp_path / "ledger.sqlite3"), FakeClock())

    with pytest.raises(ValidationError):
        await store.put_measured_grade("fast-local", 9, FakeClock().now(), "bad")

    assert await store.list_measured_grades() == ()
