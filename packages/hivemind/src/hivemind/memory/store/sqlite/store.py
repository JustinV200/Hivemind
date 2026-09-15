"""Provide SqliteMemoryStore: the durable MemoryStore built on the five memory tables.

Every mutation runs one transaction under `asyncio.to_thread` (the actual SQL lives in the sibling
modules `hivemind.memory.store.sqlite.records`, the original four tables, and
`hivemind.memory.store.sqlite.bee_bread`, the fifth) that writes the row and calls
`hivemind.pheromone.insert_event` for the accompanying event on the same connection, so the state
change and its trail event commit together (codingrules section 12), exactly the pattern
`hivemind.brood_chamber.store.sqlite.SqliteTaskStore` follows for tasks. This module owns the class
itself, `create` (which applies migrations first) and `apply_memory_migrations`; it holds no SQL of
its own beyond the one check `create` runs before it will proceed.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Constructed by a composition root once
    the Hive Manifest names the database file. Calls into hivemind.common (connect, transaction,
    migrations, errors), hivemind.cell (HoneyClearance), hivemind.memory (bee_bread, episodes,
    errors, handoff, notes, pins), hivemind.memory.store.sqlite.records and .bee_bread, and
    hivemind.pheromone only.

Key invariants:
    - `create` refuses to proceed unless `pheromone_events` already exists on `connection`'s
      database, matching `hivemind.brood_chamber.store.sqlite.SqliteTaskStore.create`'s own check.
    - Every SQLite call runs under `asyncio.to_thread`, one whole transaction per hop, serialised
      by this instance's own `asyncio.Lock` (codingrules section 11).

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions this module follows.
    - hivemind.common.sqlite and hivemind.common.migrations for connect/transaction and the
      migration runner this module builds on.
    - hivemind.memory.store.protocol for the MemoryStore protocol this class implements.
    - hivemind.memory.store.sqlite.records and .bee_bread for the SQL each method delegates to.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.common.errors import MigrationError
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.memory.bee_bread.entry import BeeBreadEntry
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.errors import BeeBreadEntryNotFoundError, ClearanceError, HandoffNotFoundError
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.memory.store.sqlite import bee_bread, records
from hivemind.pheromone import MemoryEvent
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
    """The durable MemoryStore: five SQLite tables, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has the five memory tables
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
            A SqliteMemoryStore whose five tables exist and are current.

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
            await asyncio.to_thread(records.add_pin_transaction, self._connection, pin, event)

    async def list_pins(self, allowance: HoneyClearance) -> tuple[Pin, ...]:
        """Return pins within `allowance`, oldest first; see `MemoryStore.list_pins`."""
        async with self._lock:
            rows = await asyncio.to_thread(records.select_pins_rows, self._connection, allowance)
        return tuple(Pin.model_validate_json(row["body"]) for row in rows)

    async def remove_pin(self, pin_id: EventId) -> None:
        """Remove the pin with id `pin_id`; see `MemoryStore.remove_pin`."""
        async with self._lock:
            await asyncio.to_thread(records.delete_pin, self._connection, pin_id)

    async def add_note(self, note: Note, event: MemoryEvent) -> None:
        """Insert `note`, record `event`, and evict `note.author`'s oldest if over bound."""
        async with self._lock:
            await asyncio.to_thread(records.add_note_transaction, self._connection, note, event)

    async def list_notes(
        self, author: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[Note, ...]:
        """Return notes within `allowance`, oldest first; see `MemoryStore.list_notes`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                records.select_notes_rows, self._connection, author, allowance, limit
            )
        return tuple(Note.model_validate_json(row["body"]) for row in rows)

    async def remove_note(self, note_id: EventId) -> None:
        """Remove the note with id `note_id`; see `MemoryStore.remove_note`."""
        async with self._lock:
            await asyncio.to_thread(records.delete_note, self._connection, note_id)

    async def put_handoff(
        self, event_id: EventId, handoff: Handoff, task_id: TaskId | None, event: MemoryEvent
    ) -> None:
        """Insert `handoff` keyed by `event_id` and record `event`.

        See `MemoryStore.put_handoff` for the full contract.
        """
        async with self._lock:
            await asyncio.to_thread(
                records.put_handoff_transaction, self._connection, event_id, handoff, task_id, event
            )

    async def get_handoff(self, event_id: EventId) -> tuple[Handoff, HoneyClearance]:
        """Return the stored Handoff and its clearance; see `MemoryStore.get_handoff`."""
        async with self._lock:
            row = await asyncio.to_thread(records.select_handoff_row, self._connection, event_id)
        if row is None:
            raise HandoffNotFoundError(event_id)
        return Handoff.model_validate_json(row["body"]), HoneyClearance(row["clearance"])

    async def put_episode(self, record: EpisodeRecord, event: MemoryEvent) -> None:
        """Insert `record` and record `event`; see `MemoryStore.put_episode`."""
        async with self._lock:
            await asyncio.to_thread(
                records.put_episode_transaction, self._connection, record, event
            )

    async def list_episodes(
        self, principal: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[EpisodeRecord, ...]:
        """Return episodes within `allowance`, newest first; see `MemoryStore.list_episodes`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                records.select_episodes_rows, self._connection, principal, allowance, limit
            )
        return tuple(EpisodeRecord.model_validate_json(row["body"]) for row in rows)

    async def purge_episodes_before(self, cutoff: datetime) -> int:
        """Delete episodes recorded before `cutoff`; see `MemoryStore.purge_episodes_before`."""
        async with self._lock:
            # Blocking: one DELETE in one transaction; the store's second retention DELETE.
            return await asyncio.to_thread(
                records.purge_episodes_transaction, self._connection, cutoff
            )

    async def add_bee_bread_entry(self, entry: BeeBreadEntry, event: MemoryEvent) -> None:
        """Insert `entry` and record `event`; see `MemoryStore.add_bee_bread_entry`."""
        async with self._lock:
            await asyncio.to_thread(bee_bread.add_entry_transaction, self._connection, entry, event)

    async def get_bee_bread_entry(
        self, entry_id: EventId, allowance: HoneyClearance
    ) -> BeeBreadEntry:
        """Return the entry with id `entry_id`; see `MemoryStore.get_bee_bread_entry`."""
        async with self._lock:
            row = await asyncio.to_thread(bee_bread.select_by_id_row, self._connection, entry_id)
        if row is None:
            raise BeeBreadEntryNotFoundError(entry_id)
        clearance = HoneyClearance(row["clearance"])
        if clearance.rank > allowance.rank:
            raise ClearanceError(clearance, allowance)
        return BeeBreadEntry.model_validate_json(row["body"])

    async def list_bee_bread_by_task(
        self, task_id: TaskId, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return entries for `task_id`; see `MemoryStore.list_bee_bread_by_task`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                bee_bread.select_by_task_rows, self._connection, task_id, allowance
            )
        return tuple(BeeBreadEntry.model_validate_json(row["body"]) for row in rows)

    async def list_bee_bread_between(
        self, start: datetime, end: datetime, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return entries in `[start, end]`; see `MemoryStore.list_bee_bread_between`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                bee_bread.select_between_rows, self._connection, start, end, allowance
            )
        return tuple(BeeBreadEntry.model_validate_json(row["body"]) for row in rows)


def _pheromone_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether `connection`'s database already has a `pheromone_events` table."""
    return connection.execute(_PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None
