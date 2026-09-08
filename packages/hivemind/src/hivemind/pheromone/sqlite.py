"""Provide SqlitePheromoneTrail, the durable Pheromone Trail store, and insert_event, its primitive.

The Pheromone Trail's durable home is one table, `pheromone_events`, in the Hive's single SQLite
file (ADR-0006, ADR-0007). This module owns that table end to end: the migration that creates it
(`hivemind.pheromone.migrations`), the one parametrised statement that writes a row
(`insert_event`, exported so another store's own transaction -- the Brood Chamber's, say -- can
write its state row and the matching trail event together, codingrules section 12's "same
transaction" rule), and `SqlitePheromoneTrail`, the `hivemind.pheromone.trail.PheromoneTrail`
implementation built on both. Every statement in this file is an `INSERT` or an `INSERT OR
IGNORE`: it never rewrites or removes a row it has already written, so the append-only guarantee
is provable by reading this one file rather than by trusting a convention (decision 7 in
docs/PHASE2_BRIEF.md; the removal path, the Night Veil purge, lives in its own module,
`hivemind.pheromone.retention`, on purpose).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Constructed by a composition root
    (`hivemind.cli.stores.open_trail`) once the Hive Manifest names the database file; used by
    every layer above Layer 1 that records or reads trail events. Calls into hivemind.common
    (connect, transaction, migrations) and hivemind.pheromone.events/trail/errors.

Key invariants:
    - This file issues no statement that rewrites or removes an existing row: every write is a
      plain `INSERT` (record) or `INSERT OR IGNORE` (merge_segment); a unit test reads this file's
      own source and asserts that neither of the two forbidden SQL tokens ever appears in it.
    - `insert_event` opens no transaction of its own: the caller wraps it in one, which is what
      lets `_record_transaction` below and a future cross-subsystem writer share a single commit.
    - Every SQLite call runs under `asyncio.to_thread`, one whole transaction per hop, serialised
      by this instance's own `asyncio.Lock` (codingrules section 11).

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions this module implements.
    - hivemind.common.sqlite and hivemind.common.migrations for connect/transaction and the
      migration runner this module builds on.
    - hivemind.pheromone.trail for the PheromoneTrail protocol and TrailQuery/TrailSegment.
    - hivemind.pheromone.retention for the Night Veil purge, the package's one removal path.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import datetime

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import transaction
from hivemind.pheromone.errors import DuplicateEventError
from hivemind.pheromone.events import PheromoneEvent, parse_event_json
from hivemind.pheromone.trail import TrailQuery, TrailSegment
from waggle.clock import Clock
from waggle.ids import NodeId

SUBSYSTEM = "pheromone"  # Keys this subsystem's rows in the shared schema_migrations table.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; a string,
# not a direct package import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.pheromone.migrations"

# One row's worth of parametrised placeholders, reused by both insert paths below so the column
# list and the "?" count can never drift apart between them.
_INSERT_EVENT_SQL = """
INSERT INTO pheromone_events (id, hive_id, node_id, at, family, kind, subject_id, actor, body)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
# IGNORE, not a rewrite: a row whose id already exists is left exactly as it was written; the
# statement's return still reports (via cursor.rowcount) whether a new row actually landed.
_INSERT_OR_IGNORE_EVENT_SQL = """
INSERT OR IGNORE INTO pheromone_events
    (id, hive_id, node_id, at, family, kind, subject_id, actor, body)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_BODY_SQL = "SELECT body FROM pheromone_events"  # Every read decodes the JSON body column.
# Mirrors hivemind.pheromone.trail.TRAIL_ORDER_KEY = ("at", "node_id", "id") exactly; kept in sync
# by test_sqlite_order_by_matches_trail_order_key.
_ORDER_BY_ASC = " ORDER BY at, node_id, id"
_ORDER_BY_DESC = (
    " ORDER BY at DESC, node_id DESC, id DESC"  # newest_first: the same triple, reversed.
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqlitePheromoneTrail",
    "apply_pheromone_migrations",
    "insert_event",
]


def apply_pheromone_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.pheromone.migrations`.

    Synchronous, like every function `hivemind.common.migrations` exports; `SqlitePheromoneTrail.
    create` is the one caller, and it runs this under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending; empty when the schema
        was already current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


def insert_event(connection: sqlite3.Connection, event: PheromoneEvent) -> None:
    """Insert one event's row. Opens no transaction of its own; the caller wraps it in theirs.

    This is the primitive codingrules section 12's "same transaction" rule runs on: a store
    writing its own state row (the Brood Chamber's `tasks` table, say) calls this inside its own
    `hivemind.common.sqlite.transaction` block, on its own connection to the same database file,
    so the state change and its trail event commit or roll back together.

    Args:
        connection: An open connection, already inside a transaction the caller owns.
        event: The event to insert.

    Returns:
        None, once the row is written (still inside the caller's open transaction).

    Raises:
        DuplicateEventError: `event.id` already has a row in `pheromone_events`.
    """
    try:
        connection.execute(_INSERT_EVENT_SQL, _event_row(event))
    except sqlite3.IntegrityError as exc:
        raise DuplicateEventError(
            f"event {event.id} is already on the trail (node {event.node_id})."
        ) from exc


class SqlitePheromoneTrail:
    """The durable PheromoneTrail: one SQLite table, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `pheromone_events` (normally
                produced by `create`, which applies the migration first).
            clock: Injected clock; used only to timestamp `export_segment`'s `exported_at`.
        """
        self._connection = connection
        self._clock = clock
        # Serialises every Protocol method on this instance. asyncio.to_thread may run each call
        # on a different worker thread, and sqlite3 connections are not safe for two threads to
        # issue statements on at once; this lock is what keeps "one transaction in flight at a
        # time" true despite that, matching hivemind.common.sqlite's own transaction() contract.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqlitePheromoneTrail:
        """Apply the Pheromone Trail's migrations on `connection`, then wrap it.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            clock: Injected clock, used both for migration timestamps and for this instance.

        Returns:
            A SqlitePheromoneTrail whose `pheromone_events` table exists and is current.
        """
        # Blocking: at most one transaction per pending migration (usually zero, once the schema
        # is current); runs off the event loop so a slow migration never stalls other coroutines.
        await asyncio.to_thread(apply_pheromone_migrations, connection, clock)
        return cls(connection, clock)

    async def record(self, event: PheromoneEvent) -> None:
        """Append `event`; see `PheromoneTrail.record` for the full contract."""
        async with self._lock:
            # Blocking: one INSERT in one transaction; sub-millisecond on a local SSD, though the
            # busy_timeout pragma can stretch this to BUSY_TIMEOUT_MS under write contention.
            await asyncio.to_thread(_record_transaction, self._connection, event)

    async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        """Return matching events in trail order; see `PheromoneTrail.query` for the contract."""
        async with self._lock:
            # Blocking: one indexed SELECT bounded by query.limit; expected to stay in the
            # low-single-digit milliseconds thanks to the (at, node_id, id) index.
            rows = await asyncio.to_thread(_select_events, self._connection, query)
        return tuple(parse_event_json(row["body"]) for row in rows)

    async def export_segment(self, node_id: NodeId, since: datetime | None = None) -> TrailSegment:
        """Export one node's events; see `PheromoneTrail.export_segment` for the contract."""
        async with self._lock:
            # Blocking: one indexed SELECT on node_id, optionally bounded by `since`.
            rows = await asyncio.to_thread(_select_segment_rows, self._connection, node_id, since)
        events = tuple(parse_event_json(row["body"]) for row in rows)
        return TrailSegment(node_id=node_id, exported_at=self._clock.now(), events=events)

    async def merge_segment(self, segment: TrailSegment) -> int:
        """Insert every event in `segment` not already known to this trail.

        See `PheromoneTrail.merge_segment` for the full contract.
        """
        async with self._lock:
            # Blocking: one INSERT OR IGNORE per event, all inside one transaction, so a partial
            # merge can never land -- either every unknown event is inserted, or none is.
            return await asyncio.to_thread(_merge_transaction, self._connection, segment)


def _event_row(event: PheromoneEvent) -> tuple[str, str, str, str, str, str, str, str, str]:
    """Build the nine-column parametrised row both insert statements bind.

    `at` is stored as `datetime.isoformat()`'s string, which sorts lexically the same as
    chronologically for a fixed-offset UTC timestamp (ADR-0006).
    """
    return (
        event.id,
        event.hive_id,
        event.node_id,
        event.at.isoformat(),
        event.family,
        event.kind,
        event.subject_id,
        event.actor,
        event.model_dump_json(),
    )


def _record_transaction(connection: sqlite3.Connection, event: PheromoneEvent) -> None:
    """Insert one event inside its own transaction; the sync body `record` runs under to_thread."""
    with transaction(connection):
        insert_event(connection, event)


def _select_events(connection: sqlite3.Connection, query: TrailQuery) -> list[sqlite3.Row]:
    """Build and run the filtered, ordered, limited SELECT `query` describes."""
    clauses, params = _query_where(query)
    sql = _SELECT_BODY_SQL
    if clauses:
        # clauses holds only fixed column-name literals chosen from the branches in _query_where
        # below; every value is bound through "?" in params, never interpolated into sql itself.
        sql = f"{sql} WHERE {' AND '.join(clauses)}"
    sql += _ORDER_BY_DESC if query.newest_first else _ORDER_BY_ASC
    sql += " LIMIT ?"
    params.append(query.limit)
    return connection.execute(sql, params).fetchall()


def _query_where(query: TrailQuery) -> tuple[list[str], list[object]]:
    """Translate `query`'s set fields into `WHERE` clause fragments and their `?` parameters."""
    clauses: list[str] = []
    params: list[object] = []
    if query.since is not None:
        clauses.append("at >= ?")
        params.append(query.since.isoformat())
    if query.until is not None:
        clauses.append("at <= ?")
        params.append(query.until.isoformat())
    if query.family is not None:
        clauses.append("family = ?")
        params.append(query.family)
    if query.kind is not None:
        clauses.append("kind = ?")
        params.append(query.kind)
    if query.subject_id is not None:
        clauses.append("subject_id = ?")
        params.append(query.subject_id)
    if query.node_id is not None:
        clauses.append("node_id = ?")
        params.append(query.node_id)
    return clauses, params


def _select_segment_rows(
    connection: sqlite3.Connection, node_id: NodeId, since: datetime | None
) -> list[sqlite3.Row]:
    """Select one node's events, optionally bounded by `since`, in trail order."""
    # node_id/since below are always bound through "?", never interpolated into sql itself.
    sql = f"{_SELECT_BODY_SQL} WHERE node_id = ?"
    params: list[object] = [node_id]
    if since is not None:
        sql += " AND at >= ?"
        params.append(since.isoformat())
    sql += _ORDER_BY_ASC
    return connection.execute(sql, params).fetchall()


def _merge_transaction(connection: sqlite3.Connection, segment: TrailSegment) -> int:
    """Insert every unknown event in `segment`, one INSERT OR IGNORE per event, atomically."""
    inserted = 0
    with transaction(connection):
        for event in segment.events:
            cursor = connection.execute(_INSERT_OR_IGNORE_EVENT_SQL, _event_row(event))
            # rowcount is 1 when a row actually landed, 0 when INSERT OR IGNORE silently skipped a
            # known id -- the only way to count insertions without a second query per event.
            inserted += cursor.rowcount
    return inserted
