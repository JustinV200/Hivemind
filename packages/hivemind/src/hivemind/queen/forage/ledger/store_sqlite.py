"""Provide SqliteLedgerStore, the durable LedgerStore, and its four-table schema.

The Forage ledger's durable home is four tables, `forage_ledger_capacities`,
`forage_ledger_local_reports`, `forage_ledger_grants` and `forage_ledger_reserve`, one JSON body
per row (`0001_create_forage_ledger.sql`, this module's own migration, next to it rather than in
a further `migrations/` package: `queen/forage/ledger/` already sits three levels below
`src/hivemind`, codingrules section 3's own depth limit, so a fourth-level `migrations/` package
is not available here the way `hivemind.memory.store.migrations` is).
`hivemind.common.migrations.load_migrations`
does not care whether its `.sql` files sit in a dedicated sub-package or beside the module that
applies them, since it only reads whatever in `location` matches its own filename pattern.
`SqliteLedgerStore` mirrors `hivemind.memory.store.sqlite.SqliteMemoryStore`'s own shape (one
upsert per `put_*`, one `asyncio.to_thread` per method, one lock per instance) but pairs no
`PheromoneEvent` with any write: unlike the memory tables, a ledger row is bookkeeping the
allocator reads back, not itself an audited state transition -- `hivemind.queen.forage.grants`
records the `forage.*` trail events for the transitions that matter (granted, denied, revoked,
expired) as its own, separate calls, the same way `hivemind.queen.dispatcher._record_forage_granted`
already does for a task-dispatch grant.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Constructed by a composition root once a database file is available. Calls into
    hivemind.common (connect, transaction, migrations), hivemind.forage (ForageCapacity,
    ForageGrant, RoyalReserve) and hivemind.queen.forage.ledger (model, store_protocol) only.

Key invariants:
    - Every SQLite call runs under `asyncio.to_thread`, one whole transaction per hop, serialised
      by this instance's own `asyncio.Lock` (codingrules section 11), mirroring
      `SqliteMemoryStore`'s own contract.
    - `put_*` is an upsert (`INSERT ... ON CONFLICT ... DO UPDATE`), matching `LedgerStore`'s own
      contract; `delete_grant` is idempotent.

See Also:
    - hivemind.memory.store.sqlite for SqliteMemoryStore, the pattern this module follows.
    - hivemind.queen.forage.ledger.store_protocol for LedgerStore, the protocol this implements.
    - hivemind.common.migrations for apply_migrations/load_migrations, this module's own runner.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import transaction
from hivemind.forage import ForageCapacity, ForageGrant, RoyalReserve
from hivemind.queen.forage.ledger.model import LocalPoolReport
from waggle.clock import Clock
from waggle.ids import CellId, GrantId

SUBSYSTEM = "queen_forage_ledger"  # Keys this subsystem's rows in the shared schema_migrations.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; the module
# docstring explains why they sit beside the .py modules rather than under a migrations/ package.
MIGRATIONS_PACKAGE = "hivemind.queen.forage.ledger"

_UPSERT_CAPACITY_SQL = (
    "INSERT INTO forage_ledger_capacities (cell_id, body) VALUES (?, ?) "
    "ON CONFLICT (cell_id) DO UPDATE SET body = excluded.body"
)
_SELECT_CAPACITIES_SQL = "SELECT cell_id, body FROM forage_ledger_capacities"

_UPSERT_LOCAL_REPORT_SQL = (
    "INSERT INTO forage_ledger_local_reports (warden_id, body) VALUES (?, ?) "
    "ON CONFLICT (warden_id) DO UPDATE SET body = excluded.body"
)
_SELECT_LOCAL_REPORTS_SQL = "SELECT body FROM forage_ledger_local_reports"

_UPSERT_GRANT_SQL = (
    "INSERT INTO forage_ledger_grants (grant_id, body) VALUES (?, ?) "
    "ON CONFLICT (grant_id) DO UPDATE SET body = excluded.body"
)
_DELETE_GRANT_SQL = "DELETE FROM forage_ledger_grants WHERE grant_id = ?"
_SELECT_GRANTS_SQL = "SELECT body FROM forage_ledger_grants"

_UPSERT_RESERVE_SQL = (
    "INSERT INTO forage_ledger_reserve (id, body) VALUES (1, ?) "
    "ON CONFLICT (id) DO UPDATE SET body = excluded.body"
)
_SELECT_RESERVE_SQL = "SELECT body FROM forage_ledger_reserve WHERE id = 1"

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "SqliteLedgerStore", "apply_ledger_migrations"]


def apply_ledger_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.queen.forage.ledger`.

    Synchronous, like every function `hivemind.common.migrations` exports; `SqliteLedgerStore.
    create` is the one caller, and it runs this under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteLedgerStore:
    """The durable LedgerStore: four SQLite tables, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has the four ledger tables
                (normally produced by `create`, which applies the migration first).
        """
        self._connection = connection
        # Serialises every method, matching SqliteMemoryStore's own lock.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteLedgerStore:
        """Apply this subsystem's migrations and wrap `connection`.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            clock: Injected clock, used for migration timestamps.

        Returns:
            A SqliteLedgerStore whose four tables exist and are current.
        """
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_ledger_migrations, connection, clock)
        return cls(connection)

    async def put_capacity(self, cell_id: CellId, capacity: ForageCapacity) -> None:
        """Upsert `cell_id`'s latest reported capacity; see `LedgerStore.put_capacity`."""
        async with self._lock:
            await asyncio.to_thread(
                _upsert, self._connection, _UPSERT_CAPACITY_SQL, cell_id, capacity.model_dump_json()
            )

    async def list_capacities(self) -> tuple[tuple[CellId, ForageCapacity], ...]:
        """Return every stored `(cell_id, capacity)` pair; see `LedgerStore.list_capacities`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: self._connection.execute(_SELECT_CAPACITIES_SQL).fetchall()
            )
        return tuple(
            (CellId(row["cell_id"]), ForageCapacity.model_validate_json(row["body"]))
            for row in rows
        )

    async def put_local_report(self, report: LocalPoolReport) -> None:
        """Upsert `report`, keyed by its own warden_id; see `LedgerStore.put_local_report`."""
        async with self._lock:
            await asyncio.to_thread(
                _upsert,
                self._connection,
                _UPSERT_LOCAL_REPORT_SQL,
                report.warden_id,
                report.model_dump_json(),
            )

    async def list_local_reports(self) -> tuple[LocalPoolReport, ...]:
        """Return every stored local-pool report; see `LedgerStore.list_local_reports`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: self._connection.execute(_SELECT_LOCAL_REPORTS_SQL).fetchall()
            )
        return tuple(LocalPoolReport.model_validate_json(row["body"]) for row in rows)

    async def put_grant(self, grant: ForageGrant) -> None:
        """Upsert `grant`, keyed by `grant.id`; see `LedgerStore.put_grant`."""
        async with self._lock:
            await asyncio.to_thread(
                _upsert, self._connection, _UPSERT_GRANT_SQL, grant.id, grant.model_dump_json()
            )

    async def delete_grant(self, grant_id: GrantId) -> None:
        """Remove the grant stored under `grant_id`, if any; see `LedgerStore.delete_grant`."""
        async with self._lock:
            await asyncio.to_thread(_delete_row, self._connection, grant_id)

    async def list_grants(self) -> tuple[ForageGrant, ...]:
        """Return every stored grant; see `LedgerStore.list_grants`."""
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: self._connection.execute(_SELECT_GRANTS_SQL).fetchall()
            )
        return tuple(ForageGrant.model_validate_json(row["body"]) for row in rows)

    async def put_reserve(self, reserve: RoyalReserve) -> None:
        """Replace the stored Royal Reserve; see `LedgerStore.put_reserve`."""
        async with self._lock:
            await asyncio.to_thread(_put_reserve, self._connection, reserve.model_dump_json())

    async def get_reserve(self) -> RoyalReserve | None:
        """Return the stored Royal Reserve, or None; see `LedgerStore.get_reserve`."""
        async with self._lock:
            row = await asyncio.to_thread(
                lambda: self._connection.execute(_SELECT_RESERVE_SQL).fetchone()
            )
        return RoyalReserve.model_validate_json(row["body"]) if row is not None else None


def _upsert(connection: sqlite3.Connection, sql: str, key: str, body: str) -> None:
    """Run one `(key, body)` upsert, inside its own transaction."""
    with transaction(connection):
        connection.execute(sql, (key, body))


def _delete_row(connection: sqlite3.Connection, grant_id: str) -> None:
    """Delete one grant row by id, inside its own transaction."""
    with transaction(connection):
        connection.execute(_DELETE_GRANT_SQL, (grant_id,))


def _put_reserve(connection: sqlite3.Connection, body: str) -> None:
    """Upsert the single reserve row, inside its own transaction."""
    with transaction(connection):
        connection.execute(_UPSERT_RESERVE_SQL, (body,))
