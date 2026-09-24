"""Provide SqlitePendingTable: held requests in the Entrance tables.

The durable ``PendingTable``, over ``entrance_pending`` (migration 0002) in the Hive's own database
file, through the connection every Entrance table shares (``SqliteLink``). A row is the filter
columns (status, device, times) plus ``body``, the confirmation's JSON, which every read decodes;
a settlement is one ``BEGIN IMMEDIATE`` transaction that re-reads the row, applies
``settle_pending`` and writes the result with its trail event, so of two people confirming the same
request exactly one wins, even from two processes, and no change is ever written without its
event.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.pending``. Built by
    ``SqliteEntranceStore`` over its link. Calls into ``hivemind.common.sqlite`` and the protocol's
    rules.

Key invariants:
    - ``body`` is the source of truth; ``status`` and ``created_at`` are copies written in the
      same statement, kept for filtering and ordering.

See Also:
    - hivemind.entrance.store.pending.protocol for PendingTable.
    - hivemind.entrance.store.migrations for the 0002 tables.
"""

from __future__ import annotations

import sqlite3

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import transaction
from hivemind.entrance.auth.confirm.models import PendingConfirmation, PendingId, Settlement
from hivemind.entrance.auth.confirm.state import PendingStatus
from hivemind.entrance.errors import PendingNotFoundError
from hivemind.entrance.store.link import SqliteLink
from hivemind.entrance.store.pending.protocol import check_new_pending, settle_pending
from hivemind.pheromone import GuardEvent, insert_event

_INSERT_SQL = """
INSERT INTO entrance_pending (id, device_id, status, created_at, expires_at, body)
VALUES (?, ?, ?, ?, ?, ?)
"""
_SELECT_SQL = "SELECT body FROM entrance_pending WHERE id = ?"
_UPDATE_SQL = "UPDATE entrance_pending SET status = ?, body = ? WHERE id = ?"
_LIST_SQL = "SELECT body FROM entrance_pending ORDER BY created_at, id"
_LIST_BY_STATUS_SQL = "SELECT body FROM entrance_pending WHERE status = ? ORDER BY created_at, id"

__all__ = ["SqlitePendingTable"]


class SqlitePendingTable:
    """The durable PendingTable: one table, written through the Entrance's one connection."""

    def __init__(self, link: SqliteLink) -> None:
        """Wrap the shared link; ``SqliteEntranceStore`` builds this once.

        Args:
            link: The Entrance tables' connection, thread and lock.
        """
        self._link = link

    async def put(self, pending: PendingConfirmation, event: GuardEvent) -> None:
        """Record a held request with its event; see PendingTable.put."""
        check_new_pending(pending, event)
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        await self._link.run(_insert, pending, event)

    async def get(self, pending_id: PendingId) -> PendingConfirmation:
        """Return one confirmation; see PendingTable.get."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        rows = await self._link.run(_read, _SELECT_SQL, (pending_id,))
        if not rows:
            raise PendingNotFoundError(pending_id)
        return PendingConfirmation.model_validate_json(rows[0]["body"])

    async def settle(
        self,
        pending_id: PendingId,
        expected: PendingStatus,
        settlement: Settlement,
        event: GuardEvent,
    ) -> PendingConfirmation:
        """Settle one confirmation with its event; see PendingTable.settle."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        return await self._link.run(_settle, pending_id, (expected, settlement), event)

    async def list_by_status(
        self, status: PendingStatus | None = None
    ) -> tuple[PendingConfirmation, ...]:
        """Return confirmations, oldest first; see PendingTable.list_by_status."""
        if status is None:
            # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
            rows = await self._link.run(_read, _LIST_SQL, ())
        else:
            # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
            rows = await self._link.run(_read, _LIST_BY_STATUS_SQL, (status.value,))
        return tuple(PendingConfirmation.model_validate_json(row["body"]) for row in rows)


# ──────────────────────────────────────────────────────────────────────────────
# Blocking bodies: each runs on the link's connection thread, never on the event loop.
# ──────────────────────────────────────────────────────────────────────────────


def _insert(
    connection: sqlite3.Connection, pending: PendingConfirmation, event: GuardEvent
) -> None:
    """Insert one held request and its event; a taken id or unknown device breaks an invariant."""
    params = (
        pending.id,
        pending.device_id,
        pending.status.value,
        pending.created_at.isoformat(),
        pending.expires_at.isoformat(),
        pending.model_dump_json(),
    )
    try:
        with transaction(connection):
            connection.execute(_INSERT_SQL, params)
            insert_event(connection, event)
    except sqlite3.IntegrityError as exc:
        raise InvariantViolationError(
            f"Cannot hold {pending.id}: its id is taken or device {pending.device_id} is unknown."
        ) from exc


def _read(connection: sqlite3.Connection, sql: str, params: tuple[str, ...]) -> list[sqlite3.Row]:
    """Run one read and return every row."""
    return connection.execute(sql, params).fetchall()


def _settle(
    connection: sqlite3.Connection,
    pending_id: PendingId,
    change: tuple[PendingStatus, Settlement],
    event: GuardEvent,
) -> PendingConfirmation:
    """Re-read the confirmation, apply the one settlement rule, write the result and its event."""
    expected, settlement = change
    with transaction(connection):
        row = connection.execute(_SELECT_SQL, (pending_id,)).fetchone()
        if row is None:
            raise PendingNotFoundError(pending_id)
        current = PendingConfirmation.model_validate_json(row["body"])
        settled = settle_pending(current, expected, settlement, event)
        connection.execute(
            _UPDATE_SQL, (settled.status.value, settled.model_dump_json(), pending_id)
        )
        insert_event(connection, event)
        return settled
