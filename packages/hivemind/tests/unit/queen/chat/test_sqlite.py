"""Tests for hivemind.queen.chat.sqlite: its own migration series, and lines that outlive it.

The shared behaviour is proven by tests/contracts/test_chat_log_contract.py; this module holds
what only the durable log promises: the `queen_chat` series is recorded once, the log refuses a
database with no trail table, and positions keep counting on across a fresh log over the file.

Fits into the Hive:
    Mirrors src/hivemind/queen/chat/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.chat.sqlite for the log under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.common.errors import MigrationError
from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.pheromone import SqlitePheromoneTrail
from hivemind.queen.chat import (
    SUBSYSTEM,
    ChatAuthor,
    ChatEntry,
    ChatKind,
    ChatQuery,
    SqliteChatLog,
    apply_chat_migrations,
    new_chat_entry_id,
)
from waggle.clock import FakeClock


async def _log(db: Path, clock: FakeClock) -> SqliteChatLog:
    await SqlitePheromoneTrail.create(connect(db), clock)
    return await SqliteChatLog.create(connect(db), clock)


def _notice(clock: FakeClock) -> ChatEntry:
    return ChatEntry(
        id=new_chat_entry_id(clock),
        at=clock.now(),
        author=ChatAuthor.QUEEN,
        kind=ChatKind.NOTICE,
        text="Noted.",
    )


async def test_create_records_the_queen_chat_series_once(tmp_path: Path) -> None:
    db, clock = tmp_path / "hive.sqlite3", FakeClock()
    await _log(db, clock)
    connection = connect(db)

    applied_again = apply_chat_migrations(connection, clock)

    assert SUBSYSTEM == "queen_chat"
    assert applied_versions(connection, SUBSYSTEM) == (1,)
    assert applied_again == ()


async def test_create_refuses_a_database_with_no_trail_table(tmp_path: Path) -> None:
    with pytest.raises(MigrationError):
        await SqliteChatLog.create(connect(tmp_path / "hive.sqlite3"), FakeClock())


async def test_a_fresh_log_on_the_same_file_reads_every_line_and_keeps_counting(
    tmp_path: Path,
) -> None:
    db, clock = tmp_path / "hive.sqlite3", FakeClock()
    first = await (await _log(db, clock)).append(_notice(clock))

    reopened = await SqliteChatLog.create(connect(db), clock)
    second = await reopened.append(_notice(clock))

    assert await reopened.read(ChatQuery()) == (first, second)
    assert second.seq == 2
