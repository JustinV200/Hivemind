"""Provide SqliteMemoryStore, the durable MemoryStore, and its four-table schema.

The memory tables' durable home is four tables, `memory_pins`, `memory_notes`, `memory_handoffs`
and `memory_episodes`, in the Hive's single SQLite file (ADR-0006). This module owns all four end
to end: the migration that creates them (`hivemind.memory.store.migrations`), and
`SqliteMemoryStore`, the `hivemind.memory.store.protocol.MemoryStore` implementation built on them.
Every mutation runs one transaction under `asyncio.to_thread` that writes the row and calls
`hivemind.pheromone.insert_event` for the accompanying event on the same connection, so the state
change and its trail event commit together (codingrules section 12), exactly the pattern
`hivemind.brood_chamber.store.sqlite.SqliteTaskStore` follows for tasks. `remove_pin` and
`purge_episodes_before` are two of this module's three `DELETE` statements; the third lives inside
`add_note`, evicting the oldest note past `hivemind.memory.notes.MAX_NOTES_PER_AUTHOR` for the
note's own author, in the same transaction as the insert -- a store-internal duty, not a separate
`MemoryStore` method, since nothing above the store ever needs to trigger it directly.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Constructed by a composition root once
    the Hive Manifest names the database file. Calls into hivemind.common (connect, transaction,
    migrations, errors), hivemind.cell (HoneyClearance), hivemind.memory (episodes, errors,
    handoff, notes, pins) and hivemind.pheromone (insert_event) only.

Key invariants:
    - `create` refuses to proceed unless `pheromone_events` already exists on `connection`'s
      database, matching `hivemind.brood_chamber.store.sqlite.SqliteTaskStore.create`'s own check.
    - Every SQLite call runs under `asyncio.to_thread`, one whole transaction per hop, serialised
      by this instance's own `asyncio.Lock` (codingrules section 11).
    - A row and the event that accompanies it are written inside one `hivemind.common.sqlite.
      transaction` block, so a failure partway through rolls back every write that call made.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions this module follows.
    - hivemind.common.sqlite and hivemind.common.migrations for connect/transaction and the
      migration runner this module builds on.
    - hivemind.memory.store.protocol for the MemoryStore protocol this class implements.
    - hivemind.brood_chamber.store.sqlite for SqliteTaskStore, the pattern this module mirrors.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.common.errors import MigrationError
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import transaction
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.errors import HandoffNotFoundError
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import MAX_NOTES_PER_AUTHOR, Note
from hivemind.memory.pins import Pin
from hivemind.pheromone import MemoryEvent, insert_event
from waggle.clock import Clock
from waggle.ids import EventId, TaskId

SUBSYSTEM = "memory"  # Keys this subsystem's rows in the shared schema_migrations table.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; a string,
# not a direct package import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.memory.store.migrations"

# create()'s loud-failure check, matching SqliteTaskStore.create's own guard.
_PHEROMONE_TABLE_CHECK_SQL = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pheromone_events'"
)

_INSERT_PIN_SQL = (
    "INSERT INTO memory_pins (id, clearance, source, created_at, body) VALUES (?, ?, ?, ?, ?)"
)
_DELETE_PIN_SQL = "DELETE FROM memory_pins WHERE id = ?"
_SELECT_PINS_SQL = "SELECT body FROM memory_pins"
_ORDER_PINS_BY = " ORDER BY created_at, id"

_INSERT_NOTE_SQL = (
    "INSERT INTO memory_notes (id, author, clearance, written_at, body) VALUES (?, ?, ?, ?, ?)"
)
_DELETE_NOTE_SQL = "DELETE FROM memory_notes WHERE id = ?"
_SELECT_NOTES_SQL = "SELECT body FROM memory_notes"
_SELECT_AUTHOR_NOTE_IDS_SQL = "SELECT id FROM memory_notes WHERE author = ? ORDER BY written_at, id"
_ORDER_NOTES_BY = " ORDER BY written_at, id"

_INSERT_HANDOFF_SQL = (
    "INSERT INTO memory_handoffs (event_id, task_id, clearance, written_at, body) "
    "VALUES (?, ?, ?, ?, ?)"
)
_SELECT_HANDOFF_SQL = "SELECT clearance, body FROM memory_handoffs WHERE event_id = ?"

_INSERT_EPISODE_SQL = (
    "INSERT INTO memory_episodes (id, principal, at, clearance, body) VALUES (?, ?, ?, ?, ?)"
)
_SELECT_EPISODES_SQL = "SELECT body FROM memory_episodes"
_DELETE_EPISODES_BEFORE_SQL = "DELETE FROM memory_episodes WHERE at < ?"
_ORDER_EPISODES_BY_DESC = " ORDER BY at DESC, id DESC"

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteMemoryStore",
    "apply_memory_migrations",
]


def apply_memory_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.memory.store.migrations`.

    Synchronous, like every function `hivemind.common.migrations` exports; `SqliteMemoryStore.
    create` is the one caller, and it runs this under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`, already carrying
            the `pheromone_events` table.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteMemoryStore:
    """The durable MemoryStore: four SQLite tables, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has the four memory tables
                (normally produced by `create`, which applies the migration first).
        """
        self._connection = connection
        # Serialises every method, matching SqliteTaskStore's and SqlitePheromoneTrail's own lock.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteMemoryStore:
        """Check for the Pheromone Trail's table, apply this subsystem's migrations, and wrap.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`. A composition
                root applies the Pheromone Trail's own migrations on its connection to the same
                file before calling this.
            clock: Injected clock, used for migration timestamps.

        Returns:
            A SqliteMemoryStore whose four tables exist and are current.

        Raises:
            MigrationError: `connection`'s database has no `pheromone_events` table yet.
        """
        # Blocking: a single indexed lookup against sqlite_master; sub-millisecond.
        has_pheromone_table = await asyncio.to_thread(_pheromone_table_exists, connection)
        if not has_pheromone_table:
            raise MigrationError(
                "cannot apply memory migrations: no pheromone_events table on this connection; "
                "call hivemind.pheromone.trail.sqlite.apply_pheromone_migrations (or "
                "SqlitePheromoneTrail.create) on this database file first."
            )
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_memory_migrations, connection, clock)
        return cls(connection)

    async def add_pin(self, pin: Pin, event: MemoryEvent) -> None:
        """Insert `pin` and record `event`; see `MemoryStore.add_pin`."""
        async with self._lock:
            await asyncio.to_thread(_add_pin_transaction, self._connection, pin, event)

    async def list_pins(self, allowance: HoneyClearance) -> tuple[Pin, ...]:
        """Return pins within `allowance`, oldest first; see `MemoryStore.list_pins`."""
        async with self._lock:
            rows = await asyncio.to_thread(_select_pins_rows, self._connection, allowance)
        return tuple(Pin.model_validate_json(row["body"]) for row in rows)

    async def remove_pin(self, pin_id: EventId) -> None:
        """Remove the pin with id `pin_id`; see `MemoryStore.remove_pin`."""
        async with self._lock:
            await asyncio.to_thread(_delete_row, self._connection, _DELETE_PIN_SQL, pin_id)

    async def add_note(self, note: Note, event: MemoryEvent) -> None:
        """Insert `note`, record `event`, and evict `note.author`'s oldest if over bound."""
        async with self._lock:
            await asyncio.to_thread(_add_note_transaction, self._connection, note, event)

    async def list_notes(
        self, author: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[Note, ...]:
        """Return notes within `allowance`, oldest first; see `MemoryStore.list_notes`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                _select_notes_rows, self._connection, author, allowance, limit
            )
        return tuple(Note.model_validate_json(row["body"]) for row in rows)

    async def put_handoff(
        self, event_id: EventId, handoff: Handoff, task_id: TaskId | None, event: MemoryEvent
    ) -> None:
        """Insert `handoff` keyed by `event_id` and record `event`.

        See `MemoryStore.put_handoff` for the full contract.
        """
        async with self._lock:
            await asyncio.to_thread(
                _put_handoff_transaction, self._connection, event_id, handoff, task_id, event
            )

    async def get_handoff(self, event_id: EventId) -> tuple[Handoff, HoneyClearance]:
        """Return the stored Handoff and its clearance; see `MemoryStore.get_handoff`."""
        async with self._lock:
            row = await asyncio.to_thread(_select_handoff_row, self._connection, event_id)
        if row is None:
            raise HandoffNotFoundError(event_id)
        return Handoff.model_validate_json(row["body"]), HoneyClearance(row["clearance"])

    async def put_episode(self, record: EpisodeRecord, event: MemoryEvent) -> None:
        """Insert `record` and record `event`; see `MemoryStore.put_episode`."""
        async with self._lock:
            await asyncio.to_thread(_put_episode_transaction, self._connection, record, event)

    async def list_episodes(
        self, principal: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[EpisodeRecord, ...]:
        """Return episodes within `allowance`, newest first; see `MemoryStore.list_episodes`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                _select_episodes_rows, self._connection, principal, allowance, limit
            )
        return tuple(EpisodeRecord.model_validate_json(row["body"]) for row in rows)

    async def purge_episodes_before(self, cutoff: datetime) -> int:
        """Delete episodes recorded before `cutoff`; see `MemoryStore.purge_episodes_before`."""
        async with self._lock:
            # Blocking: one DELETE in one transaction; the store's second retention DELETE.
            return await asyncio.to_thread(_purge_episodes_transaction, self._connection, cutoff)


def _pheromone_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether `connection`'s database already has a `pheromone_events` table."""
    return connection.execute(_PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None


def _allowed_clearance_values(allowance: HoneyClearance) -> tuple[str, ...]:
    """Return every HoneyClearance wire value at or below `allowance`'s rank."""
    return tuple(level.value for level in HoneyClearance if level.rank <= allowance.rank)


def _delete_row(connection: sqlite3.Connection, sql: str, row_id: str) -> None:
    """Run one parametrised DELETE by id, inside its own transaction."""
    with transaction(connection):
        connection.execute(sql, (row_id,))


def _add_pin_transaction(connection: sqlite3.Connection, pin: Pin, event: MemoryEvent) -> None:
    """Insert one pin row then its event, in one transaction; sync body run under to_thread."""
    with transaction(connection):
        connection.execute(
            _INSERT_PIN_SQL,
            (
                pin.id,
                pin.clearance.value,
                pin.source.value,
                pin.created_at.isoformat(),
                pin.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def _select_pins_rows(
    connection: sqlite3.Connection, allowance: HoneyClearance
) -> list[sqlite3.Row]:
    """Select every pin within `allowance`, in list_pins' documented order."""
    values = _allowed_clearance_values(allowance)
    placeholders = ",".join("?" for _ in values)
    sql = f"{_SELECT_PINS_SQL} WHERE clearance IN ({placeholders}){_ORDER_PINS_BY}"
    return connection.execute(sql, values).fetchall()


def _add_note_transaction(connection: sqlite3.Connection, note: Note, event: MemoryEvent) -> None:
    """Insert one note row, its event, then evict its author's oldest past the bound."""
    with transaction(connection):
        connection.execute(
            _INSERT_NOTE_SQL,
            (
                note.id,
                note.author,
                note.clearance.value,
                note.written_at.isoformat(),
                note.model_dump_json(),
            ),
        )
        insert_event(connection, event)
        _evict_oldest_notes_over_bound(connection, note.author)


def _evict_oldest_notes_over_bound(connection: sqlite3.Connection, author: str) -> None:
    """Delete `author`'s oldest notes past MAX_NOTES_PER_AUTHOR, inside the caller's transaction.

    The store's third DELETE (with `remove_pin` and `purge_episodes_before`): the per-author bound
    is enforced here, as part of `add_note`'s own transaction, rather than as a separate
    `MemoryStore` method, because nothing above this store ever needs to trigger an eviction on
    its own (codingrules 8.9: notes are "bounded", and the store is what bounds them).
    """
    rows = connection.execute(_SELECT_AUTHOR_NOTE_IDS_SQL, (author,)).fetchall()
    ids = [row["id"] for row in rows]
    # Oldest first (the SELECT's own order): drop from the front until back within the bound.
    excess = len(ids) - MAX_NOTES_PER_AUTHOR
    for stale_id in ids[: max(excess, 0)]:
        connection.execute(_DELETE_NOTE_SQL, (stale_id,))


def _select_notes_rows(
    connection: sqlite3.Connection, author: str | None, allowance: HoneyClearance, limit: int
) -> list[sqlite3.Row]:
    """Select notes within `allowance`, optionally filtered by `author`, in list_notes' order."""
    values = _allowed_clearance_values(allowance)
    clauses = [f"clearance IN ({','.join('?' for _ in values)})"]
    params: list[object] = list(values)
    if author is not None:
        clauses.append("author = ?")
        params.append(author)
    sql = f"{_SELECT_NOTES_SQL} WHERE {' AND '.join(clauses)}{_ORDER_NOTES_BY} LIMIT ?"
    params.append(limit)
    return connection.execute(sql, params).fetchall()


def _put_handoff_transaction(
    connection: sqlite3.Connection,
    event_id: str,
    handoff: Handoff,
    task_id: str | None,
    event: MemoryEvent,
) -> None:
    """Insert one handoff row then its event, in one transaction; run under to_thread."""
    with transaction(connection):
        connection.execute(
            _INSERT_HANDOFF_SQL,
            (
                event_id,
                task_id,
                handoff.clearance.value,
                event.at.isoformat(),
                handoff.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def _select_handoff_row(connection: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    """Select one handoff's clearance and body row by event id, or None when no row matches."""
    row: sqlite3.Row | None = connection.execute(_SELECT_HANDOFF_SQL, (event_id,)).fetchone()
    return row


def _put_episode_transaction(
    connection: sqlite3.Connection, record: EpisodeRecord, event: MemoryEvent
) -> None:
    """Insert one episode row then its event, in one transaction; run under to_thread."""
    with transaction(connection):
        connection.execute(
            _INSERT_EPISODE_SQL,
            (
                record.id,
                record.principal,
                record.at.isoformat(),
                record.clearance.value,
                record.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def _select_episodes_rows(
    connection: sqlite3.Connection, principal: str | None, allowance: HoneyClearance, limit: int
) -> list[sqlite3.Row]:
    """Select episodes within `allowance`, optionally filtered by `principal`, newest first."""
    values = _allowed_clearance_values(allowance)
    clauses = [f"clearance IN ({','.join('?' for _ in values)})"]
    params: list[object] = list(values)
    if principal is not None:
        clauses.append("principal = ?")
        params.append(principal)
    sql = f"{_SELECT_EPISODES_SQL} WHERE {' AND '.join(clauses)}{_ORDER_EPISODES_BY_DESC} LIMIT ?"
    params.append(limit)
    return connection.execute(sql, params).fetchall()


def _purge_episodes_transaction(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete episodes recorded before `cutoff`, in one transaction; return how many."""
    with transaction(connection):
        cursor = connection.execute(_DELETE_EPISODES_BEFORE_SQL, (cutoff.isoformat(),))
        return cursor.rowcount
