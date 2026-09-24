"""Provide SqliteChatLog, the durable ChatLog, and its migration series.

The chat lives in one append-only table, `chat_entries`, in the Hive's single SQLite file
(ADR-0006), created by this subsystem's own migration series (`queen_chat`, the numbered `.sql`
file beside this module). An append runs one transaction on the store's own `ConnectionThread`:
it assigns the next `seq` (one more than the last; lines are never deleted), writes the line, and
inserts its trail event through `hivemind.pheromone.insert_event` on the same connection when it
has one, so a human message's arrival and a reply are on the trail exactly when they are in the
log. `mark_handled` is the one other write: it stamps a waiting human message in its body and its
column together.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package. Opened by the composition root (`hivemind.cli.stores.open_chat_log`) on the
    Hive's own `[hive] db` file, after the Pheromone Trail's migrations. Calls into
    `hivemind.common` (sqlite, migrations, errors), `hivemind.pheromone` (insert_event) and the
    chat package's own model and protocol only.

Key invariants:
    - `create` refuses to proceed unless `pheromone_events` already exists on the database.
    - Every call runs on the store's own `ConnectionThread`, serialised by one `asyncio.Lock`.

See Also:
    - hivemind.queen.chat.protocol for the contract this class implements.
    - hivemind.queen.intake.sqlite for the goal-request store, built the same way.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import datetime

from hivemind.common.errors import MigrationError
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.pheromone import QueenEvent, insert_event
from hivemind.queen.chat.model import ChatAuthor, ChatEntry
from hivemind.queen.chat.protocol import ChatEntryExistsError, ChatQuery, check_chat_append
from waggle.clock import Clock

SUBSYSTEM = "queen_chat"  # Keys this series' rows in the shared schema_migrations table.
MIGRATIONS_PACKAGE = "hivemind.queen.chat"  # Where the numbered .sql file beside this one sits.

_PHEROMONE_TABLE_CHECK_SQL = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pheromone_events'"
)
_NEXT_SEQ_SQL = "SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_entries"
_INSERT_SQL = (
    "INSERT INTO chat_entries (seq, id, at, author, kind, handled_at, body) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_BY_ID_SQL = "SELECT body FROM chat_entries WHERE id = ?"
_STAMP_SQL = "UPDATE chat_entries SET handled_at = ?, body = ? WHERE id = ?"
_SELECT_PAGE_SQL = "SELECT body FROM chat_entries"
_OLDEST_FIRST = " ORDER BY seq ASC LIMIT ?"  # A page after a cursor: a stream catching up.
_NEWEST_FIRST = " ORDER BY seq DESC LIMIT ?"  # A page at the newest end: the chat view opening.
_SELECT_WAITING_SQL = (
    "SELECT body FROM chat_entries WHERE author = ? AND handled_at IS NULL ORDER BY seq LIMIT ?"
)

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "SqliteChatLog", "apply_chat_migrations"]


def apply_chat_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration in the `queen_chat` series.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions this call applied, ascending; empty once the schema is current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteChatLog:
    """The durable ChatLog: one append-only table, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `chat_entries`.
        """
        self._connection = connection
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled await
        # can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-chat")
        self._lock = asyncio.Lock()  # Serialises every method, so seq is assigned without a race.

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteChatLog:
        """Check for the Pheromone Trail's table, apply this series, and wrap `connection`.

        Args:
            connection: An open connection to a database the trail's migrations already ran on.
            clock: Injected clock, for migration timestamps.

        Returns:
            A SqliteChatLog whose table exists and is current.

        Raises:
            MigrationError: No `pheromone_events` table exists yet on this database.
        """
        # Blocking: one indexed lookup against sqlite_master; sub-millisecond.
        has_trail = await asyncio.to_thread(_pheromone_table_exists, connection)
        if not has_trail:
            raise MigrationError(
                "cannot apply queen_chat migrations: no pheromone_events table on this "
                "connection; open the Pheromone Trail on this database file first."
            )
        # Blocking: at most one transaction per pending migration (usually none, once current).
        await asyncio.to_thread(apply_chat_migrations, connection, clock)
        return cls(connection)

    async def append(self, entry: ChatEntry, event: QueenEvent | None = None) -> ChatEntry:
        """Append a fresh line (and its event); see ChatLog.append."""
        check_chat_append(entry, event)
        async with self._lock:
            # Blocking: one SELECT, one INSERT and at most one event insert, in one transaction.
            return await self._thread.run(_append_transaction, self._connection, entry, event)

    async def read(self, query: ChatQuery) -> tuple[ChatEntry, ...]:
        """Return a page, oldest first; see ChatLog.read."""
        async with self._lock:
            # Blocking: one SELECT bounded by query.limit, on the primary key or the time index.
            rows = await self._thread.run(_select_page, self._connection, query)
        return tuple(ChatEntry.model_validate_json(row["body"]) for row in rows)

    async def unhandled(self, limit: int) -> tuple[ChatEntry, ...]:
        """Return waiting human messages, oldest first; see ChatLog.unhandled."""
        async with self._lock:
            # Blocking: one indexed SELECT bounded by `limit`.
            rows = await self._thread.run(
                lambda: self._connection.execute(
                    _SELECT_WAITING_SQL, (ChatAuthor.HUMAN.value, limit)
                ).fetchall()
            )
        return tuple(ChatEntry.model_validate_json(row["body"]) for row in rows)

    async def mark_handled(self, entry_id: str, handled_at: datetime) -> None:
        """Stamp a human message handled, idempotently; see ChatLog.mark_handled."""
        async with self._lock:
            # Blocking: one SELECT and at most one UPDATE, in one transaction.
            await self._thread.run(_stamp_transaction, self._connection, entry_id, handled_at)


def _pheromone_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether `connection`'s database already has a `pheromone_events` table."""
    return connection.execute(_PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None


def _append_transaction(
    connection: sqlite3.Connection, entry: ChatEntry, event: QueenEvent | None
) -> ChatEntry:
    """Assign the next seq, insert the line and its event, in one transaction; return the line."""
    with transaction(connection):
        seq = int(connection.execute(_NEXT_SEQ_SQL).fetchone()[0])
        stored = entry.model_copy(update={"seq": seq})
        handled = stored.handled_at.isoformat() if stored.handled_at is not None else None
        row = (seq, stored.id, stored.at.isoformat(), stored.author.value, stored.kind.value)
        try:
            connection.execute(_INSERT_SQL, (*row, handled, stored.model_dump_json()))
        except sqlite3.IntegrityError as exc:
            # seq came from MAX(seq) + 1 under this write lock, so only the id can collide;
            # raising inside the block rolls the whole transaction back.
            raise ChatEntryExistsError(stored.id) from exc
        if event is not None:
            insert_event(connection, event)
    return stored


def _stamp_transaction(connection: sqlite3.Connection, entry_id: str, handled_at: datetime) -> None:
    """Stamp one waiting human message handled, in its body and its column; else do nothing."""
    with transaction(connection):
        row = connection.execute(_SELECT_BY_ID_SQL, (entry_id,)).fetchone()
        if row is None:
            return  # Unknown id: the contract's idempotent no-op.
        line = ChatEntry.model_validate_json(row["body"])
        if line.author is not ChatAuthor.HUMAN or line.handled_at is not None:
            return  # A Queen line is never handled; a handled one keeps its first stamp.
        stamped = line.model_copy(update={"handled_at": handled_at})
        connection.execute(
            _STAMP_SQL, (handled_at.isoformat(), stamped.model_dump_json(), entry_id)
        )


def _select_page(connection: sqlite3.Connection, query: ChatQuery) -> list[sqlite3.Row]:
    """Build and run the page `query` describes, returning rows oldest first."""
    clauses: list[str] = []
    params: list[object] = []
    if query.after_seq is not None:
        clauses.append("seq > ?")
        params.append(query.after_seq)
    if query.before_seq is not None:
        clauses.append("seq < ?")
        params.append(query.before_seq)
    if query.since is not None:
        clauses.append("at >= ?")
        params.append(query.since.isoformat())
    sql = _SELECT_PAGE_SQL
    if clauses:
        # clauses holds only the fixed literals above; every value is bound through "?".
        sql = f"{sql} WHERE {' AND '.join(clauses)}"
    # Anchored after a cursor: the oldest `limit` from there; otherwise the newest `limit`, which
    # the reversal below turns back into oldest first.
    sql += _OLDEST_FIRST if query.after_seq is not None else _NEWEST_FIRST
    rows: list[sqlite3.Row] = connection.execute(sql, (*params, query.limit)).fetchall()
    return rows if query.after_seq is not None else rows[::-1]
