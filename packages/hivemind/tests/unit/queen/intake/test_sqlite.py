"""Tests for hivemind.queen.intake.sqlite: its own migration series, and rows that outlive it.

The shared behaviour is proven by tests/contracts/test_goal_request_store_contract.py; this
module holds what only the durable store promises: the `queen_intake` series is recorded once,
the store refuses a database with no trail table, and a row committed by one store is read back
by a fresh one on the same file, which is what lets a goal request survive a crash.

Fits into the Hive:
    Mirrors src/hivemind/queen/intake/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.intake.sqlite for the store under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.human import make_goal_request

from hivemind.common.errors import MigrationError
from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.pheromone import QueenEvent, SqlitePheromoneTrail
from hivemind.queen.intake import (
    SUBSYSTEM,
    SqliteGoalRequestStore,
    apply_intake_migrations,
)
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id


async def _store(db: Path, clock: FakeClock) -> SqliteGoalRequestStore:
    await SqlitePheromoneTrail.create(connect(db), clock)
    return await SqliteGoalRequestStore.create(connect(db), clock)


async def test_create_records_the_queen_intake_series_once(tmp_path: Path) -> None:
    db, clock = tmp_path / "hive.sqlite3", FakeClock()
    await _store(db, clock)
    connection = connect(db)

    applied_again = apply_intake_migrations(connection, clock)

    assert SUBSYSTEM == "queen_intake"
    assert applied_versions(connection, SUBSYSTEM) == (1,)
    assert applied_again == ()


async def test_create_refuses_a_database_with_no_trail_table(tmp_path: Path) -> None:
    with pytest.raises(MigrationError):
        await SqliteGoalRequestStore.create(connect(tmp_path / "hive.sqlite3"), FakeClock())


async def test_a_committed_request_is_read_back_by_a_fresh_store_on_the_same_file(
    tmp_path: Path,
) -> None:
    db, clock = tmp_path / "hive.sqlite3", FakeClock()
    request = make_goal_request(clock)
    event = QueenEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="queen.goal_request_received",
        subject_id=new_hive_id(clock),
        payload={"goal_request_id": request.id},
    )
    await (await _store(db, clock)).insert(request, event)

    reopened = await SqliteGoalRequestStore.create(connect(db), clock)

    assert await reopened.get(request.id) == request
