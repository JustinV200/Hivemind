"""Tests for hivemind.common.migrations: loading, applying and recording numbered SQL migrations.

Fits into the Hive:
    Mirrors src/hivemind/common/migrations.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.migrations for the module under test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hivemind.common.errors import MigrationError
from hivemind.common.migrations import (
    Migration,
    _apply_one,  # private: exercised directly below to reach one lock-contention edge case
    applied_versions,
    apply_migrations,
    load_migrations,
)
from hivemind.common.sqlite import connect
from waggle.clock import FakeClock

# A minimal, always-valid migration body; tests that only care about version bookkeeping never
# need a real schema.
_NOOP_SQL = "CREATE TABLE placeholder (id INTEGER);"


def _write_migration(directory: Path, filename: str, sql: str = _NOOP_SQL) -> None:
    """Drop one migration file into `directory`, the way a real subsystem's package would."""
    (directory / filename).write_text(sql, encoding="utf-8")


def _open(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh file-backed connection under tmp_path, never a shared fixture file."""
    return connect(tmp_path / "hive.sqlite3")


# ──────────────────────────────────────────────────────────────────────────────
# load_migrations
# ──────────────────────────────────────────────────────────────────────────────


def test_load_migrations_orders_files_by_version_not_filesystem_order(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0002_second.sql")
    _write_migration(tmp_path, "0001_first.sql")

    migrations = load_migrations(tmp_path)

    assert [migration.version for migration in migrations] == [1, 2]
    assert [migration.name for migration in migrations] == ["first", "second"]


def test_load_migrations_ignores_files_that_do_not_match_the_pattern(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0001_first.sql")
    _write_migration(tmp_path, "__init__.py", sql="")
    _write_migration(tmp_path, "README.md", sql="not a migration")
    (tmp_path / "not_a_file.sql").mkdir()

    migrations = load_migrations(tmp_path)

    assert [migration.version for migration in migrations] == [1]


def test_load_migrations_raises_on_a_gap_in_the_version_series(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0001_first.sql")
    _write_migration(tmp_path, "0003_third.sql")

    with pytest.raises(MigrationError, match="contiguously"):
        load_migrations(tmp_path)


def test_load_migrations_raises_when_the_series_does_not_start_at_one(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0002_second.sql")

    with pytest.raises(MigrationError, match="contiguously"):
        load_migrations(tmp_path)


def test_load_migrations_raises_on_a_duplicate_version(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0001_first.sql")
    _write_migration(tmp_path, "0001_again.sql")

    with pytest.raises(MigrationError, match="duplicate migration version 1"):
        load_migrations(tmp_path)


# ──────────────────────────────────────────────────────────────────────────────
# applied_versions / apply_migrations
# ──────────────────────────────────────────────────────────────────────────────


def test_applied_versions_is_empty_before_anything_has_ever_been_applied(tmp_path: Path) -> None:
    connection = _open(tmp_path)

    assert applied_versions(connection, "pheromone") == ()


def test_apply_migrations_applies_a_pending_migration_and_records_it(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    clock = FakeClock()
    migration = Migration(version=1, name="create_widgets", sql=_NOOP_SQL)

    applied = apply_migrations(connection, "pheromone", (migration,), clock)

    assert applied == (1,)
    row = connection.execute(
        "SELECT subsystem, version, name, applied_at FROM schema_migrations"
    ).fetchone()
    assert row["subsystem"] == "pheromone"
    assert row["version"] == 1
    assert row["name"] == "create_widgets"
    # applied_at comes straight from the injected clock, not wall-clock time.
    assert row["applied_at"] == clock.now().isoformat()
    # the migration's own DDL actually ran, not just its record.
    connection.execute("INSERT INTO placeholder (id) VALUES (1)")


def test_apply_migrations_is_idempotent_on_a_second_call(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    clock = FakeClock()
    migration = Migration(version=1, name="create_widgets", sql=_NOOP_SQL)
    apply_migrations(connection, "pheromone", (migration,), clock)

    second_call = apply_migrations(connection, "pheromone", (migration,), clock)

    assert second_call == ()


def test_apply_migrations_applies_only_versions_not_yet_recorded(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    clock = FakeClock()
    first = Migration(version=1, name="create_widgets", sql=_NOOP_SQL)
    apply_migrations(connection, "pheromone", (first,), clock)
    second = Migration(version=2, name="create_gadgets", sql="CREATE TABLE gadgets (id INTEGER);")

    applied = apply_migrations(connection, "pheromone", (first, second), clock)

    assert applied == (2,)
    assert applied_versions(connection, "pheromone") == (1, 2)


def test_apply_migrations_raises_when_a_recorded_migration_was_renamed(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    clock = FakeClock()
    original = Migration(version=1, name="create_widgets", sql=_NOOP_SQL)
    apply_migrations(connection, "pheromone", (original,), clock)
    renamed = Migration(version=1, name="create_widgets_v2", sql=_NOOP_SQL)

    with pytest.raises(MigrationError, match="create_widgets"):
        apply_migrations(connection, "pheromone", (renamed,), clock)


def test_apply_migrations_rolls_back_a_broken_migration_and_records_nothing(
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path)
    clock = FakeClock()
    broken = Migration(
        version=1,
        name="broken",
        sql="CREATE TABLE widgets (id INTEGER); THIS IS NOT VALID SQL;",
    )

    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(connection, "pheromone", (broken,), clock)

    # Neither half of the failed migration survived: no row recorded, and the table its first
    # (valid) statement created was rolled back along with the rest of the script.
    assert applied_versions(connection, "pheromone") == ()
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'widgets'"
    ).fetchone()
    assert table is None


def test_apply_migrations_raises_if_the_connection_already_has_a_transaction_open(
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path)
    clock = FakeClock()
    migration = Migration(version=1, name="create_widgets", sql=_NOOP_SQL)
    connection.execute("BEGIN IMMEDIATE")

    with pytest.raises(RuntimeError, match="already has a transaction in progress"):
        apply_migrations(connection, "pheromone", (migration,), clock)

    connection.execute("ROLLBACK")


def test_apply_one_skips_rollback_when_begin_immediate_itself_never_opens(
    tmp_path: Path,
) -> None:
    """Cover the one case where `_apply_one`'s ROLLBACK guard must stay silent.

    A migration script's own BEGIN IMMEDIATE can itself fail to acquire the lock (contended by
    another writer) rather than any statement after it; issuing ROLLBACK anyway would raise a
    second error ("cannot rollback - no transaction is active") on top of the original one.
    """
    db_path = tmp_path / "hive.sqlite3"
    connection = connect(db_path)
    connection.execute("PRAGMA busy_timeout = 50")  # fail fast; the real 5000ms would just wait
    clock = FakeClock()
    migration = Migration(version=1, name="create_widgets", sql=_NOOP_SQL)

    # A second, independent connection holding an EXCLUSIVE lock is what makes the migration's
    # BEGIN IMMEDIATE itself fail, rather than a statement inside the script.
    blocker = sqlite3.connect(str(db_path), isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _apply_one(connection, "pheromone", migration, clock)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # No second error from an unguarded ROLLBACK, and nothing left open on our connection either.
    assert connection.in_transaction is False
