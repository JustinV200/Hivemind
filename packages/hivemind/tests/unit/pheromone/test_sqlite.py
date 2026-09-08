"""Tests for hivemind.pheromone.sqlite: SqlitePheromoneTrail, insert_event, and migrations.

The four PheromoneTrail Protocol methods (record, query, export_segment, merge_segment) get their
full behavioural coverage from tests/contracts/test_pheromone_trail_contract.py, parametrised
over this implementation and MemoryPheromoneTrail together. This module covers what is specific to
the SQLite adapter: the append-only file-scan guarantee (decision 7), migration idempotence,
insert_event's transaction-sharing contract, restart durability, and the ORDER BY/TRAIL_ORDER_KEY
consistency check.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/sqlite.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.sqlite for the module under test.
    - tests/contracts/test_pheromone_trail_contract.py for the shared PheromoneTrail behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hivemind.common.sqlite import connect, transaction
from hivemind.pheromone import sqlite as pheromone_sqlite
from hivemind.pheromone.errors import DuplicateEventError
from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.sqlite import SqlitePheromoneTrail, insert_event
from hivemind.pheromone.trail import TRAIL_ORDER_KEY, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_hive_id, new_node_id

# Whole-word, case-insensitive: decision 7 forbids either token anywhere in sqlite.py's own text.
_FORBIDDEN_TOKENS = re.compile(r"\b(update|delete)\b", re.IGNORECASE)


def _make_cell_event(clock: FakeClock) -> CellEvent:
    """Build a well-formed CellEvent on a fresh node id."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=new_cell_id(clock),
        payload={},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Append-only file scan (decision 7)
# ──────────────────────────────────────────────────────────────────────────────


def test_sqlite_module_source_contains_no_update_or_delete_token() -> None:
    source = Path(pheromone_sqlite.__file__).read_text(encoding="utf-8")

    assert _FORBIDDEN_TOKENS.search(source) is None


def test_sqlite_order_by_matches_trail_order_key() -> None:
    expected_asc = " ORDER BY " + ", ".join(TRAIL_ORDER_KEY)
    expected_desc = " ORDER BY " + ", ".join(f"{column} DESC" for column in TRAIL_ORDER_KEY)

    assert expected_asc == pheromone_sqlite._ORDER_BY_ASC
    assert expected_desc == pheromone_sqlite._ORDER_BY_DESC


# ──────────────────────────────────────────────────────────────────────────────
# create() / migrations
# ──────────────────────────────────────────────────────────────────────────────


async def test_create_applies_the_migration_and_is_idempotent(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")

    first = await SqlitePheromoneTrail.create(connection, clock)
    second = await SqlitePheromoneTrail.create(connection, clock)

    assert isinstance(first, SqlitePheromoneTrail)
    assert isinstance(second, SqlitePheromoneTrail)
    rows = connection.execute(
        "SELECT version FROM schema_migrations WHERE subsystem = 'pheromone'"
    ).fetchall()
    assert [row["version"] for row in rows] == [1]


# ──────────────────────────────────────────────────────────────────────────────
# insert_event
# ──────────────────────────────────────────────────────────────────────────────


async def test_insert_event_raises_duplicate_event_error_on_a_known_id(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    await SqlitePheromoneTrail.create(connection, clock)
    event = _make_cell_event(clock)
    with transaction(connection):
        insert_event(connection, event)

    with pytest.raises(DuplicateEventError), transaction(connection):
        insert_event(connection, event)

    row_count = connection.execute("SELECT COUNT(*) AS n FROM pheromone_events").fetchone()["n"]
    assert row_count == 1


async def test_insert_event_rolls_back_with_the_callers_transaction(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    await SqlitePheromoneTrail.create(connection, clock)
    event = _make_cell_event(clock)

    with pytest.raises(ValueError, match="boom"), transaction(connection):
        insert_event(connection, event)
        raise ValueError("boom")

    rows = connection.execute("SELECT id FROM pheromone_events").fetchall()
    assert rows == []


# ──────────────────────────────────────────────────────────────────────────────
# Restart durability
# ──────────────────────────────────────────────────────────────────────────────


async def test_events_survive_closing_and_reopening_the_same_file(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    connection = connect(db_path)
    trail = await SqlitePheromoneTrail.create(connection, clock)
    event = _make_cell_event(clock)
    await trail.record(event)
    connection.close()

    reopened_connection = connect(db_path)
    reopened_trail = await SqlitePheromoneTrail.create(reopened_connection, clock)
    events = await reopened_trail.query(TrailQuery())

    assert events == (event,)
