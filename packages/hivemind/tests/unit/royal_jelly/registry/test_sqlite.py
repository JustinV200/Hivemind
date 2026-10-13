"""Tests for hivemind.royal_jelly.registry.sqlite: the migration, the table's own guarantees.

The behaviour every registry must share is in the contract suite; this module tests what only the
SQLite store has: the numbered migration, the unique indexes, the immutability triggers, and rows
that survive a close and reopen of the database file.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/registry/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.registry.sqlite for the module under test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from builders.royal_jelly import make_offer, make_tool_spec

from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.royal_jelly.registry import (
    CombToolRow,
    QueenPrincipal,
    SqliteCombRegistry,
    ToolKey,
    apply_comb_registry_migrations,
)
from hivemind.royal_jelly.registry.sql import INSERT_SQL, row_to_values
from hivemind.royal_jelly.registry.sqlite import SUBSYSTEM
from waggle.clock import FakeClock
from waggle.messages.tool.authoring import ToolScope


async def _promoted(registry: SqliteCombRegistry, version: str = "1.0.0") -> CombToolRow:
    spec = make_tool_spec(version=version)
    report, offer = make_offer(spec)
    change = await registry.promote(spec, report, offer, QueenPrincipal(), "initial")
    return change.row


def test_the_migration_applies_once_and_is_idempotent() -> None:
    connection = connect(":memory:")

    first = apply_comb_registry_migrations(connection, FakeClock())
    second = apply_comb_registry_migrations(connection, FakeClock())

    assert first == (1,)
    assert second == ()
    assert applied_versions(connection, SUBSYSTEM) == (1,)


async def test_create_applies_migrations_without_needing_the_pheromone_trail() -> None:
    connection = connect(":memory:")

    await SqliteCombRegistry.create(connection, FakeClock())

    assert connection.execute("SELECT count(*) FROM comb_tools").fetchone()[0] == 0


async def test_a_row_and_its_current_flag_survive_closing_and_reopening_the_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hive.db"
    registry = await SqliteCombRegistry.create(connect(path), FakeClock())
    row = await _promoted(registry)

    reopened = await SqliteCombRegistry.create(connect(path), FakeClock())
    got = await reopened.get(row.name, ToolScope.HIVE, None)

    assert got == row
    assert got.is_current


async def test_hive_scope_is_stored_as_the_empty_cell_text_never_null() -> None:
    connection = connect(":memory:")
    registry = await SqliteCombRegistry.create(connection, FakeClock())
    await _promoted(registry)

    stored = connection.execute("SELECT cell_id FROM comb_tools").fetchone()[0]

    assert stored == ""


async def test_the_unique_index_refuses_a_second_hive_row_at_the_same_version() -> None:
    connection = connect(":memory:")
    registry = await SqliteCombRegistry.create(connection, FakeClock())
    row = await _promoted(registry)

    # Bypassing the store on purpose: the database itself must refuse the duplicate.
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(INSERT_SQL, row_to_values(row))


async def test_the_database_refuses_two_current_versions_of_one_tool() -> None:
    connection = connect(":memory:")
    registry = await SqliteCombRegistry.create(connection, FakeClock())
    await _promoted(registry, "1.0.0")
    second = await _promoted(registry, "2.0.0")

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE comb_tools SET is_current = 1 WHERE version = ?", ("1.0.0",))
    assert second.is_current


async def test_the_database_refuses_a_current_retired_row() -> None:
    connection = connect(":memory:")
    registry = await SqliteCombRegistry.create(connection, FakeClock())
    await _promoted(registry)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE comb_tools SET state = 'RETIRED', retired_at = 'x'")


@pytest.mark.parametrize(
    "column", ["spec_json", "package_sha256", "report_json", "promoted_by", "reason", "version"]
)
async def test_the_evidence_columns_cannot_be_rewritten(column: str) -> None:
    connection = connect(":memory:")
    registry = await SqliteCombRegistry.create(connection, FakeClock())
    await _promoted(registry)

    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute(f"UPDATE comb_tools SET {column} = 'tampered'")  # noqa: S608  # fixed names


async def test_a_row_can_never_be_deleted_only_retired() -> None:
    connection = connect(":memory:")
    registry = await SqliteCombRegistry.create(connection, FakeClock())
    row = await _promoted(registry)
    await registry.retire(ToolKey(row.name, ToolScope.HIVE, None), "1.0.0", QueenPrincipal(), "bye")

    with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
        connection.execute("DELETE FROM comb_tools")
    assert connection.execute("SELECT count(*) FROM comb_tools").fetchone()[0] == 1


async def test_a_retired_row_cannot_be_revived_by_direct_update() -> None:
    connection = connect(":memory:")
    registry = await SqliteCombRegistry.create(connection, FakeClock())
    row = await _promoted(registry)
    await registry.retire(ToolKey(row.name, ToolScope.HIVE, None), "1.0.0", QueenPrincipal(), "bye")

    with pytest.raises(sqlite3.DatabaseError, match="cannot change state"):
        connection.execute("UPDATE comb_tools SET state = 'PROMOTED'")
