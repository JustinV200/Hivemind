"""Tests for hivemind.common.sqlite: connect()'s pragmas and the transaction() context manager.

Fits into the Hive:
    Mirrors src/hivemind/common/sqlite.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.sqlite for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.common.sqlite import BUSY_TIMEOUT_MS, connect, transaction


def test_connect_enables_wal_journal_mode_on_a_file_database(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")

    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode.lower() == "wal"


def test_connect_leaves_an_in_memory_database_off_wal() -> None:
    connection = connect(":memory:")

    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode.lower() != "wal"


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")

    enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert enabled == 1


def test_connect_sets_the_busy_timeout_pragma(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")

    timeout_ms = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert timeout_ms == BUSY_TIMEOUT_MS


def test_connect_returns_rows_addressable_by_column_name(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")

    row = connection.execute("SELECT 1 AS one").fetchone()

    assert row["one"] == 1


def test_connect_opens_in_autocommit_mode_with_no_transaction_pending(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")

    assert connection.isolation_level is None
    assert connection.in_transaction is False


def test_transaction_commits_its_writes_on_a_clean_exit(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")
    connection.execute("CREATE TABLE widgets (id INTEGER)")

    with transaction(connection):
        connection.execute("INSERT INTO widgets (id) VALUES (1)")

    rows = connection.execute("SELECT id FROM widgets").fetchall()
    assert [row["id"] for row in rows] == [1]


def test_transaction_rolls_back_every_write_when_the_block_raises(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")
    connection.execute("CREATE TABLE widgets (id INTEGER)")

    with pytest.raises(ValueError, match="boom"), transaction(connection):
        connection.execute("INSERT INTO widgets (id) VALUES (1)")
        raise ValueError("boom")

    rows = connection.execute("SELECT id FROM widgets").fetchall()
    assert rows == []
    assert connection.in_transaction is False


def test_transaction_refuses_to_nest_on_a_connection_already_in_one(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")

    # Both context managers' __enter__ run left to right before the body; the second
    # transaction(connection) is the one that must raise, since the first already opened one.
    with (
        pytest.raises(RuntimeError, match="already has one in progress"),
        transaction(connection),
        transaction(connection),
    ):
        pass  # never reached; the second transaction()'s __enter__ raises first
