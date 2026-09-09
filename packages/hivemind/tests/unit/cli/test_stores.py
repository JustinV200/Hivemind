"""Tests for hivemind.cli.stores: open_trail and open_chamber, the CLI's store composition root.

Fits into the Hive:
    Mirrors src/hivemind/cli/stores.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.stores for the module under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, TaskFilter
from hivemind.cli.stores import DEFAULT_DB, open_chamber, open_trail
from hivemind.pheromone import SqlitePheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from waggle.clock import SystemClock
from waggle.ids import new_hive_id, new_node_id


def _identity() -> ChamberIdentity:
    clock = SystemClock()
    return ChamberIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")


def test_default_db_is_hive_sqlite3_in_the_current_directory() -> None:
    assert Path("hive.sqlite3") == DEFAULT_DB


def test_open_trail_returns_a_ready_sqlite_trail_on_a_fresh_file(tmp_path: Path) -> None:
    # open_trail runs its own asyncio.run internally (it is a sync composition function, matching
    # a typer command body), so this test stays sync too rather than nesting event loops.
    trail = open_trail(tmp_path / "hive.sqlite3")

    assert isinstance(trail, SqlitePheromoneTrail)
    assert asyncio.run(trail.query(TrailQuery())) == ()


def test_open_chamber_applies_both_subsystems_migrations_on_a_fresh_file(tmp_path: Path) -> None:
    chamber = open_chamber(tmp_path / "hive.sqlite3", _identity())

    assert isinstance(chamber, BroodChamber)
    assert asyncio.run(chamber.list(TaskFilter())) == ()


def test_open_chamber_works_on_a_file_open_trail_already_migrated(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    open_trail(db)  # migrates pheromone_events first, as a `hive trail` command would

    chamber = open_chamber(db, _identity())

    assert asyncio.run(chamber.list(TaskFilter())) == ()
