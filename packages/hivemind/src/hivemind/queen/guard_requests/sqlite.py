"""Provide SqliteGuardRequestStore, the durable GuardRequestStore, and its migration series.

The Queen's Guard requests live in one table, `guard_requests`, in the Hive's single SQLite file
(ADR-0006), created by this subsystem's own migration series (`queen_guard_requests`, the numbered
`.sql` file beside this module, the same placement `hivemind.queen.intake` uses). Every write runs
one transaction on the store's own `ConnectionThread`, so the door's `file` has committed before it
returns (ADR-0043: "durable before it returns") and a decision's stamp and its hold land together.
No write records a trail event (`protocol`'s module docstring says why), so unlike the goal-request
table this one needs no `pheromone_events` table on its connection.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Opened by the composition root (`hivemind.cli.compose.guard.
    build_guard_deps`) on the Hive's own `[hive] db` file. Calls into `hivemind.common` (sqlite,
    migrations) and the sub-package's own model and protocol only.

Key invariants:
    - Every call runs on the store's own `ConnectionThread`, serialised by one `asyncio.Lock`.
    - A duplicate report id writes nothing (`INSERT OR IGNORE`); a decided row is never re-stamped.

See Also:
    - hivemind.queen.guard_requests.protocol for the contract this class implements.
    - hivemind.queen.intake.sqlite for SqliteGoalRequestStore, the pattern this class mirrors.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import datetime

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.queen.guard_requests.model import GuardDecision, GuardRequest, PlacementHold
from hivemind.queen.guard_requests.protocol import DEFAULT_PENDING_PAGE
from waggle.clock import Clock
from waggle.ids import CellId

SUBSYSTEM = "queen_guard_requests"  # Keys this series' rows in the shared schema_migrations table.
MIGRATIONS_PACKAGE = "hivemind.queen.guard_requests"  # Where the numbered .sql file sits.

_INSERT_SQL = (
    "INSERT OR IGNORE INTO guard_requests "
    "(id, filed_at, decided_at, hold_cell_id, hold_released_at, body) VALUES (?, ?, ?, ?, ?, ?)"
)
_UPDATE_SQL = (
    "UPDATE guard_requests SET decided_at = ?, hold_cell_id = ?, hold_released_at = ?, body = ? "
    "WHERE id = ?"
)
_SELECT_ONE_SQL = "SELECT body FROM guard_requests WHERE id = ?"
_PENDING_SQL = (
    "SELECT body FROM guard_requests WHERE decided_at IS NULL ORDER BY filed_at, id LIMIT ?"
)
_HOLDS_SQL = (
    "SELECT body FROM guard_requests WHERE hold_cell_id IS NOT NULL "
    "AND hold_released_at IS NULL ORDER BY decided_at, id"
)
_CELL_HOLDS_SQL = (
    "SELECT body FROM guard_requests WHERE hold_cell_id = ? AND hold_released_at IS NULL "
    "ORDER BY decided_at, id"
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteGuardRequestStore",
    "apply_guard_request_migrations",
]


def apply_guard_request_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration in the `queen_guard_requests` series.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions this call applied, ascending; empty once the schema is current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteGuardRequestStore:
    """The durable GuardRequestStore: one table, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `guard_requests`.
        """
        self._connection = connection
        # One thread per connection: a cancelled await never leaves a transaction open.
        self._thread = ConnectionThread("hive-guard-requests")
        self._lock = asyncio.Lock()  # Serialises every method, like every other SQLite store.

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteGuardRequestStore:
        """Apply this series and wrap `connection`.

        Args:
            connection: An open connection to the Hive's database file.
            clock: Injected clock, for migration timestamps.

        Returns:
            A SqliteGuardRequestStore whose table exists and is current.
        """
        # Blocking: at most one transaction per pending migration (usually none, once current).
        await asyncio.to_thread(apply_guard_request_migrations, connection, clock)
        return cls(connection)

    async def file(self, request: GuardRequest) -> bool:
        """Write a fresh request unless its report is filed; see GuardRequestStore.file."""
        async with self._lock:
            # Blocking: one INSERT OR IGNORE, committed before this returns (durable filing).
            return await self._thread.run(_insert, self._connection, request)

    async def get(self, report_id: str) -> GuardRequest | None:
        """Return one request, or None; see GuardRequestStore.get."""
        async with self._lock:
            # Blocking: one SELECT by primary key.
            rows = await self._thread.run(_select, self._connection, _SELECT_ONE_SQL, (report_id,))
        return GuardRequest.model_validate_json(rows[0]["body"]) if rows else None

    async def pending(self, limit: int = DEFAULT_PENDING_PAGE) -> tuple[GuardRequest, ...]:
        """Return undecided requests, oldest first; see GuardRequestStore.pending."""
        async with self._lock:
            # Blocking: one indexed SELECT bounded by `limit`.
            rows = await self._thread.run(_select, self._connection, _PENDING_SQL, (limit,))
        return tuple(GuardRequest.model_validate_json(row["body"]) for row in rows)

    async def decide(
        self, report_id: str, decision: GuardDecision, hold: PlacementHold | None = None
    ) -> GuardRequest | None:
        """Stamp a decision once; see GuardRequestStore.decide."""
        async with self._lock:
            # Blocking: one SELECT and at most one UPDATE, in one transaction.
            return await self._thread.run(_decide, self._connection, report_id, (decision, hold))

    async def holds(self) -> tuple[PlacementHold, ...]:
        """Return every active hold, oldest first; see GuardRequestStore.holds."""
        async with self._lock:
            # Blocking: one indexed SELECT; holds are rare (a Hive Stand fallback each).
            rows = await self._thread.run(_select, self._connection, _HOLDS_SQL, ())
        requests = (GuardRequest.model_validate_json(row["body"]) for row in rows)
        return tuple(request.hold for request in requests if request.hold is not None)

    async def release_holds(
        self, cell_id: CellId, released_at: datetime
    ) -> tuple[PlacementHold, ...]:
        """Release every active hold on `cell_id`; see GuardRequestStore.release_holds."""
        async with self._lock:
            # Blocking: one SELECT and one UPDATE per held request, in one transaction.
            return await self._thread.run(_release, self._connection, cell_id, released_at)


def _columns(request: GuardRequest) -> tuple[str | None, ...]:
    """The mirrored columns plus the body, in `_UPDATE_SQL`'s order (before the id)."""
    decided = request.decision.decided_at.isoformat() if request.decision is not None else None
    hold = request.hold
    released = hold.released_at.isoformat() if hold and hold.released_at else None
    return (decided, hold.cell_id if hold else None, released, request.model_dump_json())


def _insert(connection: sqlite3.Connection, request: GuardRequest) -> bool:
    """Insert the row unless its id exists; return whether it was written."""
    with transaction(connection):
        decided, hold_cell, released, body = _columns(request)
        row = (request.id, request.filed_at.isoformat(), decided, hold_cell, released, body)
        cursor = connection.execute(_INSERT_SQL, row)
    return cursor.rowcount == 1


def _select(
    connection: sqlite3.Connection, sql: str, params: tuple[object, ...]
) -> list[sqlite3.Row]:
    """Run one fixed SELECT (every value bound through `?`) and return its rows."""
    rows: list[sqlite3.Row] = connection.execute(sql, params).fetchall()
    return rows


def _decide(
    connection: sqlite3.Connection,
    report_id: str,
    stamp: tuple[GuardDecision, PlacementHold | None],
) -> GuardRequest | None:
    """Stamp the decision on a pending row in one transaction; return the stored row."""
    decision, hold = stamp
    with transaction(connection):
        rows = _select(connection, _SELECT_ONE_SQL, (report_id,))
        if not rows:
            return None
        request = GuardRequest.model_validate_json(rows[0]["body"])
        if not request.is_pending:
            return request  # Decided already: its first decision stands.
        decided = GuardRequest(
            report=request.report, filed_at=request.filed_at, decision=decision, hold=hold
        )
        connection.execute(_UPDATE_SQL, (*_columns(decided), report_id))
    return decided


def _release(
    connection: sqlite3.Connection, cell_id: CellId, released_at: datetime
) -> tuple[PlacementHold, ...]:
    """Release every active hold on `cell_id` in one transaction; return them as released."""
    released: list[PlacementHold] = []
    with transaction(connection):
        for row in _select(connection, _CELL_HOLDS_SQL, (cell_id,)):
            request = GuardRequest.model_validate_json(row["body"])
            if request.hold is None:
                continue  # The mirrored column and the body always agree; defensive only.
            done = request.hold.model_copy(update={"released_at": released_at})
            relabelled = request.model_copy(update={"hold": done})
            connection.execute(_UPDATE_SQL, (*_columns(relabelled), request.id))
            released.append(done)
    return tuple(released)
