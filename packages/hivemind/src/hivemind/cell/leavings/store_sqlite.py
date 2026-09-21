"""Provide SqliteLeavingsStore, the durable LeavingsStore, and its one-table schema.

The Leavings ledger's durable home is one table, `cell_leavings`, in the Hive's single SQLite file
(ADR-0006), keyed by `(cell_id, path)`. This module owns both end to end: the migration that
creates it (`hivemind.cell.leavings.migrations`), and `SqliteLeavingsStore`, the
`hivemind.cell.leavings.store_protocol.LeavingsStore` implementation built on it. Every mutation
runs one transaction on the store's `ConnectionThread` that writes the row and calls
`hivemind.pheromone.insert_event` for the accompanying event on the same connection, mirroring
`hivemind.brood_chamber.store.sqlite.SqliteTaskStore` exactly (roadmap step 5.0a: "its SQLite
store in the Brood Chamber's store pattern").

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell.leavings`.
    Constructed by a composition root (`hivemind.cli.stores.open_leavings`) once the Hive Manifest
    names the database file. Calls into hivemind.common (connect, transaction, migrations,
    errors), hivemind.cell (errors, leavings.model, leavings.store_protocol) and hivemind.pheromone
    (insert_event) only.

Key invariants:
    - `create` refuses to proceed unless `pheromone_events` already exists on `connection`'s
      database, the same loud-failure check `SqliteTaskStore.create` makes, and for the same
      reason: this store writes `cell.left`/`cell.leaving_removed` events into that table.
    - Every SQLite call runs on the store's `ConnectionThread`, one transaction per hop, serialised
      by this instance's own `asyncio.Lock` (codingrules section 11).
    - A row and the event that accompanies it are written inside one
      `hivemind.common.sqlite.transaction` block, so a failure partway through rolls back both.
    - `record_leaving` upserts (`INSERT OR REPLACE`) rather than rejecting an existing row: an
      active row's own `prior` always survives the replace (coordinator review, roadmap step
      5.0a bug fix), read back inside the same transaction before the write so it can never race
      a concurrent removal.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions this module follows.
    - hivemind.brood_chamber.store.sqlite for SqliteTaskStore, the pattern this module mirrors.
    - hivemind.cell.leavings.store_protocol for LeavingsStore and check_leaving_event, the guard
      every mutation calls before writing.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import datetime
from pathlib import Path

from hivemind.cell.errors import LeavingAlreadyRemovedError, LeavingNotFoundError
from hivemind.cell.leavings.model import ApprovedBy, Leaving
from hivemind.cell.leavings.store_protocol import check_leaving_event
from hivemind.common.errors import MigrationError
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.pheromone import CellEvent, insert_event
from waggle.clock import Clock
from waggle.ids import CellId

SUBSYSTEM = "cell_leavings"  # Keys this subsystem's rows in the shared schema_migrations table.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; a string,
# not a direct package import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.cell.leavings.migrations"

_PHEROMONE_TABLE_CHECK_SQL = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pheromone_events'"
)

_INSERT_SQL = """
INSERT OR REPLACE INTO cell_leavings
    (cell_id, path, sha256, size, task_id, lease_id, approved_by, reason, prior, left_at,
     removed_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
"""
_SELECT_ACTIVE_SQL = (
    "SELECT * FROM cell_leavings WHERE cell_id = ? AND path = ? AND removed_at IS NULL"
)
_SELECT_ALL_FOR_PATH_SQL = "SELECT * FROM cell_leavings WHERE cell_id = ? AND path = ?"
_SELECT_LIST_ACTIVE_SQL = (
    "SELECT * FROM cell_leavings WHERE cell_id = ? AND removed_at IS NULL ORDER BY left_at, path"
)
_SELECT_LIST_ALL_SQL = "SELECT * FROM cell_leavings WHERE cell_id = ? ORDER BY left_at, path"
_MARK_REMOVED_SQL = (
    "UPDATE cell_leavings SET removed_at = ? WHERE cell_id = ? AND path = ? AND removed_at IS NULL"
)

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "SqliteLeavingsStore", "apply_leavings_migrations"]


def apply_leavings_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.cell.leavings.migrations`.

    Synchronous, like every function `hivemind.common.migrations` exports; `SqliteLeavingsStore.
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


class SqliteLeavingsStore:
    """The durable LeavingsStore: one SQLite table, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `cell_leavings` (normally
                produced by `create`, which applies the migration first).
        """
        self._connection = connection
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled
        # await can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-leavings")
        # Serialises every method on this instance, matching SqliteTaskStore's own lock.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteLeavingsStore:
        """Check for the Pheromone Trail's table, apply this subsystem's migrations, and wrap.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`. A composition
                root applies the Pheromone Trail's own migrations on its connection to the same
                file before calling this.
            clock: Injected clock, used for migration timestamps.

        Returns:
            A SqliteLeavingsStore whose `cell_leavings` table exists and is current.

        Raises:
            MigrationError: `connection`'s database has no `pheromone_events` table yet.
        """
        # Blocking: a single indexed lookup against sqlite_master; sub-millisecond.
        has_pheromone_table = await asyncio.to_thread(_pheromone_table_exists, connection)
        if not has_pheromone_table:
            raise MigrationError(
                "cannot apply cell_leavings migrations: no pheromone_events table on this "
                "connection; call hivemind.pheromone.trail.sqlite.apply_pheromone_migrations (or "
                "SqlitePheromoneTrail.create) on this database file first."
            )
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_leavings_migrations, connection, clock)
        return cls(connection)

    async def record_leaving(self, leaving: Leaving, event: CellEvent) -> None:
        """Upsert `leaving`'s row and record `event`; see LeavingsStore.record_leaving."""
        check_leaving_event(leaving.cell_id, event)
        async with self._lock:
            # Blocking: one existence check, one INSERT OR REPLACE, one event insert, one
            # transaction.
            await self._thread.run(_record_leaving_transaction, self._connection, leaving, event)

    async def get_leaving(self, cell_id: CellId, path: Path) -> Leaving:
        """Return the active Leaving at `(cell_id, path)`; see LeavingsStore.get_leaving."""
        async with self._lock:
            row = await self._thread.run(
                lambda: self._connection.execute(
                    _SELECT_ACTIVE_SQL, (cell_id, str(path))
                ).fetchone()
            )
        if row is None:
            raise LeavingNotFoundError(cell_id, path)
        return _row_to_leaving(row)

    async def list_leavings(
        self, cell_id: CellId, *, include_removed: bool = False
    ) -> tuple[Leaving, ...]:
        """Return every Leaving for `cell_id`; see LeavingsStore.list_leavings."""
        sql = _SELECT_LIST_ALL_SQL if include_removed else _SELECT_LIST_ACTIVE_SQL
        async with self._lock:
            rows = await self._thread.run(
                lambda: self._connection.execute(sql, (cell_id,)).fetchall()
            )
        return tuple(_row_to_leaving(row) for row in rows)

    async def mark_removed(
        self, cell_id: CellId, path: Path, removed_at: datetime, event: CellEvent
    ) -> Leaving:
        """Mark the active Leaving at `(cell_id, path)` removed; see LeavingsStore.mark_removed."""
        check_leaving_event(cell_id, event)
        async with self._lock:
            # Blocking: one UPDATE, one existence re-check on a 0-rowcount UPDATE, one event
            # insert, one transaction.
            return await self._thread.run(
                _mark_removed_transaction, self._connection, cell_id, path, removed_at, event
            )


def _pheromone_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether `connection`'s database already has a `pheromone_events` table."""
    return connection.execute(_PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None


def _row_to_leaving(row: sqlite3.Row) -> Leaving:
    """Decode one `cell_leavings` row into a Leaving."""
    return Leaving(
        cell_id=CellId(row["cell_id"]),
        path=Path(row["path"]),
        sha256=row["sha256"],
        size=row["size"],
        task_id=row["task_id"],
        lease_id=row["lease_id"],
        approved_by=ApprovedBy(row["approved_by"]),
        reason=row["reason"],
        prior=row["prior"],
        left_at=datetime.fromisoformat(row["left_at"]),
        removed_at=datetime.fromisoformat(row["removed_at"]) if row["removed_at"] else None,
    )


def _record_leaving_transaction(
    connection: sqlite3.Connection, leaving: Leaving, event: CellEvent
) -> None:
    """Upsert the row, keeping an existing active row's own `prior`, then insert the event."""
    with transaction(connection):
        existing = connection.execute(
            _SELECT_ACTIVE_SQL, (leaving.cell_id, str(leaving.path))
        ).fetchone()
        # An active row already covers this exact path (the same lease noting it twice, or a
        # second goal run while an earlier run's row is still active, coordinator review): every
        # field but `prior` is replaced by `leaving`'s own; `prior` must stay what the path held
        # before any Leaving ever existed there, so `remove` still replays the true original.
        prior = existing["prior"] if existing is not None else leaving.prior
        connection.execute(
            _INSERT_SQL,
            (
                leaving.cell_id,
                str(leaving.path),
                leaving.sha256,
                leaving.size,
                leaving.task_id,
                leaving.lease_id,
                leaving.approved_by.value,
                leaving.reason,
                prior,
                leaving.left_at.isoformat(),
            ),
        )
        insert_event(connection, event)


def _mark_removed_transaction(
    connection: sqlite3.Connection,
    cell_id: CellId,
    path: Path,
    removed_at: datetime,
    event: CellEvent,
) -> Leaving:
    """Update one row's removed_at, then insert its event, in one transaction; run on the thread."""
    with transaction(connection):
        cursor = connection.execute(_MARK_REMOVED_SQL, (removed_at.isoformat(), cell_id, str(path)))
        if cursor.rowcount != 1:
            # 0 rows changed: either no row at all, or one that is already removed -- distinguish
            # them with a second, unfiltered lookup so the error names the right condition.
            existing = connection.execute(_SELECT_ALL_FOR_PATH_SQL, (cell_id, str(path))).fetchone()
            if existing is None:
                raise LeavingNotFoundError(cell_id, path)
            raise LeavingAlreadyRemovedError(cell_id, path)
        insert_event(connection, event)
        row = connection.execute(_SELECT_ALL_FOR_PATH_SQL, (cell_id, str(path))).fetchone()
        return _row_to_leaving(row)
