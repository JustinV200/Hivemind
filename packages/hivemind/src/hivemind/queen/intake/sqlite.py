"""Provide SqliteGoalRequestStore, the durable GoalRequestStore, and its migration series.

The Queen's goal requests live in one table, `goal_requests`, in the Hive's single SQLite file
(ADR-0006), created by this subsystem's own migration series (`queen_intake`, the numbered `.sql`
file beside this module, the same placement `hivemind.queen.cluster.orders` uses). Every write
runs one transaction on the store's own `ConnectionThread` that writes the row and calls
`hivemind.pheromone.insert_event` for its `queen.goal_request_*` event on the same connection, so
the state change and its trail event commit together (codingrules Appendix C), exactly as the
Brood Chamber's `SqliteTaskStore` does.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Opened by the composition root (`hivemind.cli.stores.open_goal_requests`) on the
    Hive's own `[hive] db` file, after the Pheromone Trail's migrations. Calls into
    `hivemind.common` (sqlite, migrations, errors), `hivemind.pheromone` (insert_event) and the
    intake package's own errors, model and protocol only.

Key invariants:
    - `create` refuses to proceed unless `pheromone_events` already exists on the connection's
      database: every write here inserts an event into that table.
    - Every call runs on the store's own `ConnectionThread`, serialised by one `asyncio.Lock`.

See Also:
    - hivemind.queen.intake.protocol for the contract this class implements.
    - hivemind.brood_chamber.store.sqlite for SqliteTaskStore, the pattern this class mirrors.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3

from hivemind.common.errors import MigrationError
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.pheromone import QueenEvent, insert_event
from hivemind.queen.intake.errors import GoalRequestExistsError, GoalRequestNotFoundError
from hivemind.queen.intake.model import GoalRequest
from hivemind.queen.intake.protocol import GoalRequestQuery, check_goal_request_event
from waggle.clock import Clock

SUBSYSTEM = "queen_intake"  # Keys this series' rows in the shared schema_migrations table.
MIGRATIONS_PACKAGE = "hivemind.queen.intake"  # Where the numbered .sql file beside this one sits.

_PHEROMONE_TABLE_CHECK_SQL = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pheromone_events'"
)
_INSERT_SQL = (
    "INSERT INTO goal_requests (id, state, device_id, goal_id, received_at, finished_at, body) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_UPDATE_SQL = (
    "UPDATE goal_requests SET state = ?, device_id = ?, goal_id = ?, received_at = ?, "
    "finished_at = ?, body = ? WHERE id = ?"
)
_SELECT_ONE_SQL = "SELECT body FROM goal_requests WHERE id = ?"
_SELECT_MANY_SQL = "SELECT body FROM goal_requests"
_ORDER_BY = " ORDER BY received_at, id"  # GoalRequestStore.list_requests's documented order.

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteGoalRequestStore",
    "apply_intake_migrations",
]


def apply_intake_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration in the `queen_intake` series.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions this call applied, ascending; empty once the schema is current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteGoalRequestStore:
    """The durable GoalRequestStore: one table, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `goal_requests`.
        """
        self._connection = connection
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled await
        # can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-intake")
        self._lock = asyncio.Lock()  # Serialises every method, like SqliteTaskStore's own.

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteGoalRequestStore:
        """Check for the Pheromone Trail's table, apply this series, and wrap `connection`.

        Args:
            connection: An open connection to a database the trail's migrations already ran on.
            clock: Injected clock, for migration timestamps.

        Returns:
            A SqliteGoalRequestStore whose table exists and is current.

        Raises:
            MigrationError: No `pheromone_events` table exists yet on this database.
        """
        # Blocking: one indexed lookup against sqlite_master; sub-millisecond.
        has_trail = await asyncio.to_thread(_pheromone_table_exists, connection)
        if not has_trail:
            raise MigrationError(
                "cannot apply queen_intake migrations: no pheromone_events table on this "
                "connection; open the Pheromone Trail on this database file first."
            )
        # Blocking: at most one transaction per pending migration (usually none, once current).
        await asyncio.to_thread(apply_intake_migrations, connection, clock)
        return cls(connection)

    async def insert(self, request: GoalRequest, event: QueenEvent) -> None:
        """Insert a fresh request with its event; see GoalRequestStore.insert."""
        check_goal_request_event(request, event)
        async with self._lock:
            # Blocking: one row insert plus one event insert, in one transaction.
            await self._thread.run(_insert_transaction, self._connection, request, event)

    async def update(self, request: GoalRequest, event: QueenEvent) -> None:
        """Replace the stored request and record `event`; see GoalRequestStore.update."""
        check_goal_request_event(request, event)
        async with self._lock:
            # Blocking: one UPDATE plus one event insert, in one transaction.
            await self._thread.run(_update_transaction, self._connection, request, event)

    async def get(self, request_id: str) -> GoalRequest:
        """Return the stored request; see GoalRequestStore.get."""
        async with self._lock:
            # Blocking: one SELECT by primary key.
            row = await self._thread.run(_select_one, self._connection, request_id)
        if row is None:
            raise GoalRequestNotFoundError(request_id)
        return GoalRequest.model_validate_json(row["body"])

    async def list_requests(self, query: GoalRequestQuery) -> tuple[GoalRequest, ...]:
        """Return every matching request, oldest first; see GoalRequestStore.list_requests."""
        async with self._lock:
            # Blocking: one indexed SELECT bounded by query.limit.
            rows = await self._thread.run(_select_many, self._connection, query)
        return tuple(GoalRequest.model_validate_json(row["body"]) for row in rows)


def _pheromone_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether `connection`'s database already has a `pheromone_events` table."""
    return connection.execute(_PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None


def _columns(request: GoalRequest) -> tuple[str | None, ...]:
    """Build the mirrored columns plus the body, in `_INSERT_SQL`'s order after `id`."""
    finished = request.finished_at.isoformat() if request.finished_at is not None else None
    return (
        request.state.value,
        request.device_id,
        request.goal_id,
        request.received_at.isoformat(),
        finished,
        request.model_dump_json(),
    )


def _insert_transaction(
    connection: sqlite3.Connection, request: GoalRequest, event: QueenEvent
) -> None:
    """Insert the row and its event in one transaction; a duplicate id writes neither."""
    with transaction(connection):
        try:
            connection.execute(_INSERT_SQL, (request.id, *_columns(request)))
        except sqlite3.IntegrityError as exc:
            # The primary key is the only constraint this insert can break; raising inside the
            # block rolls back the whole transaction before the event is ever written.
            raise GoalRequestExistsError(request.id) from exc
        insert_event(connection, event)


def _update_transaction(
    connection: sqlite3.Connection, request: GoalRequest, event: QueenEvent
) -> None:
    """Replace the row and insert its event in one transaction; a missing row writes neither."""
    with transaction(connection):
        cursor = connection.execute(_UPDATE_SQL, (*_columns(request), request.id))
        # rowcount 0 means no row had this id; raising inside the block rolls the event back too.
        if cursor.rowcount == 0:
            raise GoalRequestNotFoundError(request.id)
        insert_event(connection, event)


def _select_one(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
    """Return the one row for `request_id`, or None."""
    row: sqlite3.Row | None = connection.execute(_SELECT_ONE_SQL, (request_id,)).fetchone()
    return row


def _select_many(connection: sqlite3.Connection, query: GoalRequestQuery) -> list[sqlite3.Row]:
    """Build and run the filtered, ordered, limited SELECT `query` describes."""
    clauses: list[str] = []
    params: list[object] = []
    if query.state is not None:
        clauses.append("state = ?")
        params.append(query.state.value)
    if query.device_id is not None:
        clauses.append("device_id = ?")
        params.append(query.device_id)
    if query.unfinished:
        clauses.append("finished_at IS NULL")
    sql = _SELECT_MANY_SQL
    if clauses:
        # clauses holds only the fixed literals above; every value is bound through "?".
        sql = f"{sql} WHERE {' AND '.join(clauses)}"
    params.append(query.limit)
    rows: list[sqlite3.Row] = connection.execute(f"{sql}{_ORDER_BY} LIMIT ?", params).fetchall()
    return rows
