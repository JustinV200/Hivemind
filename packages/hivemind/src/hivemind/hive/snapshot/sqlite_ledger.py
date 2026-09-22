"""Define SqliteSnapshotLedger: the durable SnapshotLedgerPort, so a rollback can outlive a process.

Roadmap step 5.10's own follow-up gap: `hivemind.hive.snapshot.ledger.SnapshotLedger` is in-memory
only, so `hive cells rollback` run as a separate CLI process after an earlier `hive cells snapshot`
cannot see what that process recorded. `SqliteSnapshotLedger` is the same `SnapshotLedgerPort`
(record/get/disk_used_bytes/oldest_for_cell/room_for/expire/delete/delete_for_cell), backed by one
`snapshot_records` table instead of an in-memory dict, so two CLI invocations against the same
`[hive] db` file see the same book.

Deliberately synchronous, not `async def` behind `hivemind.common.sqlite.ConnectionThread`
(codingrules section 11's usual "blocking sqlite3 under asyncio.to_thread" rule): every method
`SnapshotLedgerPort` declares is synchronous already, because `hivemind.hive.snapshot.docker.
DockerSnapshotter`/`.qemu.QemuSnapshotter` already call their shared ledger synchronously from
inside their own async `snapshot`/`rollback` (`self._ledger.record(...)`, never `await`'d) --
making this implementation's own methods `async def` would break the one contract both backends
and the Queen's own `hivemind.queen.cell_gate.snapshot.CellSnapshotHandler` already share, and
neither is in this dispatch's own file list to widen. Each call is therefore a small, synchronous,
single-row read or write directly on the caller's own thread: a documented trade-off, not an
oversight (this dispatch's own report flags it) -- acceptable because a snapshot is taken at most a
handful of times per risky Capping proposal, never on a hot path.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.snapshot`. Built by
    `hivemind.cli.stores.open_snapshot_ledger` and handed wherever a composition root needs a
    `SnapshotLedgerPort`: `hivemind.hive.snapshot.snapshotter_for` and `hivemind.queen.cell_gate.
    snapshot.CellSnapshotHandler`. Calls into `hivemind.cell` (SnapshotId), `hivemind.common.
    migrations`, `hivemind.hive.snapshot.ledger` (SnapshotLedgerPort, SnapshotNotFoundError,
    SnapshotRecord) and waggle only.

Key invariants:
    - Satisfies `hivemind.hive.snapshot.ledger.SnapshotLedgerPort` method for method; a contract
      test runs the same assertions against this class and `SnapshotLedger` alike.
    - `create` applies this subsystem's migration before returning (mirrors `hivemind.queen.
      cluster.orders.SqliteOrderStore.create`'s own shape, synchronously -- module docstring).
    - `get`/`delete` raise `SnapshotNotFoundError` for an id this table does not (or no longer)
      hold; every other query degrades to an empty result instead of raising, matching
      `SnapshotLedger`'s own contract exactly.

See Also:
    - hivemind.hive.snapshot.ledger for SnapshotLedgerPort/SnapshotLedger/SnapshotRecord, the
      contract and the in-memory sibling this class matches.
    - hivemind.common.migrations for apply_migrations/load_migrations, this module's own callers.
    - hivemind.cli.stores for open_snapshot_ledger, this class's one composition-root caller.
"""

from __future__ import annotations

import importlib.resources
import sqlite3
from datetime import datetime

from hivemind.cell import SnapshotId
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.hive.snapshot.ledger import SnapshotNotFoundError, SnapshotRecord
from waggle.clock import Clock
from waggle.ids import CellId

SUBSYSTEM = "hive_snapshot_ledger"  # Keys this subsystem's rows in the shared schema_migrations.
MIGRATIONS_PACKAGE = "hivemind.hive.snapshot"  # Where the numbered .sql file beside this one sits.

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteSnapshotLedger",
    "apply_snapshot_migrations",
]

_INSERT_SQL = (
    "INSERT INTO snapshot_records (id, cell_id, taken_at, bytes_estimate, expires_at) "
    "VALUES (?, ?, ?, ?, ?)"
)
_SELECT_ONE_SQL = (
    "SELECT id, cell_id, taken_at, bytes_estimate, expires_at FROM snapshot_records WHERE id = ?"
)
_SELECT_BY_CELL_SQL = (
    "SELECT id, cell_id, taken_at, bytes_estimate, expires_at FROM snapshot_records "
    "WHERE cell_id = ?"
)
_SELECT_EXPIRED_SQL = (
    "SELECT id, cell_id, taken_at, bytes_estimate, expires_at FROM snapshot_records "
    "WHERE expires_at <= ?"
)
_DELETE_ONE_SQL = "DELETE FROM snapshot_records WHERE id = ?"
_DELETE_BY_CELL_SQL = "DELETE FROM snapshot_records WHERE cell_id = ?"


def apply_snapshot_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.hive.snapshot`.

    Synchronous, like every function `hivemind.common.migrations` exports and like the rest of
    this module (module docstring); `SqliteSnapshotLedger.create` is the one caller.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteSnapshotLedger:
    """The durable SnapshotLedgerPort: one `snapshot_records` table, one open connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `snapshot_records` (normally
                produced by `create`, which applies the migration first).
        """
        self._connection = connection

    @classmethod
    def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteSnapshotLedger:
        """Apply this subsystem's migration and wrap `connection`.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            clock: Injected clock, used for the migration's own timestamp.

        Returns:
            A SqliteSnapshotLedger whose `snapshot_records` table exists and is current.
        """
        apply_snapshot_migrations(connection, clock)
        return cls(connection)

    def record(self, record: SnapshotRecord) -> None:
        """Add `record`; see `SnapshotLedgerPort.record`."""
        self._connection.execute(
            _INSERT_SQL,
            (
                record.id,
                record.cell_id,
                record.taken_at.isoformat(),
                record.bytes_estimate,
                record.expires_at.isoformat(),
            ),
        )

    def get(self, snapshot_id: SnapshotId) -> SnapshotRecord:
        """Return the record for `snapshot_id`; see `SnapshotLedgerPort.get`."""
        row = self._connection.execute(_SELECT_ONE_SQL, (snapshot_id,)).fetchone()
        if row is None:
            raise SnapshotNotFoundError(snapshot_id)
        return _record_from_row(row)

    def disk_used_bytes(self, cell_id: CellId) -> int:
        """Total bytes every live snapshot for `cell_id` accounts for; see `SnapshotLedgerPort`."""
        return sum(record.bytes_estimate for record in self._for_cell(cell_id))

    def oldest_for_cell(self, cell_id: CellId) -> SnapshotId | None:
        """Oldest live snapshot id for `cell_id`, or None; see `SnapshotLedgerPort`."""
        records = self._for_cell(cell_id)
        if not records:
            return None
        return min(records, key=lambda record: record.taken_at).id

    def room_for(
        self, cell_id: CellId, incoming_bytes: int, budget_bytes: int | None
    ) -> SnapshotId | None:
        """The snapshot id to evict for `incoming_bytes`, or None; see `SnapshotLedgerPort`."""
        if budget_bytes is None:
            return None
        if self.disk_used_bytes(cell_id) + incoming_bytes <= budget_bytes:
            return None
        return self.oldest_for_cell(cell_id)

    def expire(self, now: datetime) -> tuple[SnapshotId, ...]:
        """Remove and return every snapshot expired at or before `now`; see `SnapshotLedgerPort`."""
        rows = self._connection.execute(_SELECT_EXPIRED_SQL, (now.isoformat(),)).fetchall()
        expired = tuple(SnapshotId(row["id"]) for row in rows)
        for snapshot_id in expired:
            self._connection.execute(_DELETE_ONE_SQL, (snapshot_id,))
        return expired

    def delete(self, snapshot_id: SnapshotId) -> None:
        """Remove one snapshot's record; see `SnapshotLedgerPort.delete`."""
        cursor = self._connection.execute(_DELETE_ONE_SQL, (snapshot_id,))
        if cursor.rowcount == 0:
            raise SnapshotNotFoundError(snapshot_id)

    def delete_for_cell(self, cell_id: CellId) -> tuple[SnapshotId, ...]:
        """Remove and return every snapshot recorded for `cell_id`; see `SnapshotLedgerPort`."""
        ids = tuple(record.id for record in self._for_cell(cell_id))
        self._connection.execute(_DELETE_BY_CELL_SQL, (cell_id,))
        return ids

    def _for_cell(self, cell_id: CellId) -> tuple[SnapshotRecord, ...]:
        """Return every record for `cell_id`, in no particular order."""
        rows = self._connection.execute(_SELECT_BY_CELL_SQL, (cell_id,)).fetchall()
        return tuple(_record_from_row(row) for row in rows)


def _record_from_row(row: sqlite3.Row) -> SnapshotRecord:
    """Build a SnapshotRecord from one `snapshot_records` row."""
    return SnapshotRecord(
        id=SnapshotId(row["id"]),
        cell_id=CellId(row["cell_id"]),
        taken_at=datetime.fromisoformat(row["taken_at"]),
        bytes_estimate=row["bytes_estimate"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )
