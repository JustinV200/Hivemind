"""Define SqliteRequeeningStore: the RequeeningStore over the Hive's own SQLite file.

The durable `RequeeningStore` (ADR-0062): three small tables beside the Brood Chamber in `[hive]
db`, created by this package's own numbered migration (`0001_create_queen_requeening.sql`, in this
directory, the way `queen/cluster/` keeps its own beside `orders.py`). The snapshot is one row whose
body is the `QueenSnapshot` as JSON; each handled envelope is one row keyed by sender and envelope
id, its instant kept as integer microseconds so a window query compares numbers, never strings;
each listener's last port is one row by name. Every call runs on the store's own
`ConnectionThread`, one at a time under one lock, like every other Queen store.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.
    requeening.store`. Implements `.protocol.RequeeningStore`; opened by
    `hivemind.cli.stores.open_requeening_store`. Calls into `hivemind.common` (migrations,
    sqlite), `hivemind.queen.requeening.models` and waggle only.

Key invariants:
    - The snapshot table never holds more than one row (its CHECK says so too).
    - A plain handled note never replaces a row that carries a question's wiring.
    - Every method runs under `self._lock` on `self._thread`, so a cancelled await never leaves a
      statement half-run under the next caller.

See Also:
    - hivemind.queen.requeening.store.memory for the in-process implementation.
    - hivemind.queen.cluster.orders for the shape this store follows.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import UTC, datetime

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread
from hivemind.queen.requeening.models import HandledEnvelope, QueenSnapshot
from waggle.clock import Clock

SUBSYSTEM = "queen_requeening"  # Keys this subsystem's rows in the shared schema_migrations.
MIGRATIONS_PACKAGE = "hivemind.queen.requeening.store"  # Where the numbered .sql file sits.
_US_PER_S = 1_000_000  # Instants are kept as integer microseconds since the epoch, in UTC.

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "SqliteRequeeningStore"]

_PUT_SNAPSHOT_SQL = (
    "INSERT INTO queen_snapshot (slot, taken_at, body) VALUES (1, ?, ?) "
    "ON CONFLICT (slot) DO UPDATE SET taken_at = excluded.taken_at, body = excluded.body"
)
_GET_SNAPSHOT_SQL = "SELECT body FROM queen_snapshot WHERE slot = 1"
# A plain note is ignored for a known envelope; a note with wiring replaces a plain one.
_NOTE_PLAIN_SQL = (
    "INSERT OR IGNORE INTO queen_handled "
    "(principal, envelope_id, handled_us, body, has_question) VALUES (?, ?, ?, ?, 0)"
)
_NOTE_WIRED_SQL = (
    "INSERT OR REPLACE INTO queen_handled "
    "(principal, envelope_id, handled_us, body, has_question) VALUES (?, ?, ?, ?, 1)"
)
_SINCE_SQL = "SELECT body FROM queen_handled WHERE handled_us >= ? ORDER BY handled_us, envelope_id"
_PRUNE_SQL = "DELETE FROM queen_handled WHERE handled_us < ?"
_GET_PORT_SQL = "SELECT port FROM queen_listener_ports WHERE name = ?"
_PUT_PORT_SQL = (
    "INSERT INTO queen_listener_ports (name, port) VALUES (?, ?) "
    "ON CONFLICT (name) DO UPDATE SET port = excluded.port"
)


class SqliteRequeeningStore:
    """The durable RequeeningStore: three tables, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has this package's tables.
        """
        self._connection = connection
        self._thread = ConnectionThread("hive-requeening")
        self._lock = asyncio.Lock()  # Serialises every method, like every other Queen store.

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteRequeeningStore:
        """Apply this package's migration and wrap `connection`.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            clock: Stamps each applied migration's `applied_at`.

        Returns:
            A store whose tables exist and are current.
        """
        migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
        await asyncio.to_thread(apply_migrations, connection, SUBSYSTEM, migrations, clock)
        return cls(connection)

    async def put_snapshot(self, snapshot: QueenSnapshot) -> None:
        """Replace the kept snapshot; see `RequeeningStore.put_snapshot`."""
        params = (snapshot.taken_at.isoformat(), snapshot.model_dump_json())
        await self._run(_PUT_SNAPSHOT_SQL, params)

    async def latest_snapshot(self) -> QueenSnapshot | None:
        """Return the kept snapshot; see `RequeeningStore.latest_snapshot`."""
        rows = await self._run(_GET_SNAPSHOT_SQL, ())
        return QueenSnapshot.model_validate_json(str(rows[0][0])) if rows else None

    async def note_handled(self, row: HandledEnvelope) -> None:
        """Record `row`, keeping a question's wiring; see `RequeeningStore.note_handled`."""
        sql = _NOTE_PLAIN_SQL if row.question is None else _NOTE_WIRED_SQL
        params = (row.principal, row.envelope_id, _us(row.handled_at), row.model_dump_json())
        await self._run(sql, params)

    async def handled_since(self, since: datetime) -> tuple[HandledEnvelope, ...]:
        """Return the rows handled at or after `since`; see `RequeeningStore.handled_since`."""
        rows = await self._run(_SINCE_SQL, (_us(since),))
        return tuple(HandledEnvelope.model_validate_json(str(row[0])) for row in rows)

    async def prune_handled(self, before: datetime) -> int:
        """Forget rows handled before `before`; see `RequeeningStore.prune_handled`."""
        async with self._lock:
            # Latency: one indexed local delete; the row count comes back with it.
            cursor = await self._thread.run(self._connection.execute, _PRUNE_SQL, (_us(before),))
        return int(cursor.rowcount)

    async def recall_port(self, name: str) -> int | None:
        """Return the port `name` bound last; see `RequeeningStore.recall_port`."""
        rows = await self._run(_GET_PORT_SQL, (name,))
        return int(rows[0][0]) if rows else None

    async def remember_port(self, name: str, port: int) -> None:
        """Keep `port` for `name`; see `RequeeningStore.remember_port`."""
        await self._run(_PUT_PORT_SQL, (name, port))

    async def _run(self, sql: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
        """Run one statement under the lock on the store's thread; return what it selected."""
        async with self._lock:
            # Latency: one local SQLite statement, autocommitted; no network, no timeout needed.
            return await self._thread.run(lambda: self._connection.execute(sql, params).fetchall())


def _us(at: datetime) -> int:
    """Return `at` as integer microseconds since the epoch, in UTC."""
    return round(at.astimezone(UTC).timestamp() * _US_PER_S)
