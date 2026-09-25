"""Provide SqliteLoginTable: login failures and known networks in the Entrance tables.

The durable ``LoginTable``, over ``entrance_login_failures`` and ``entrance_device_networks``
(migration 0002) in the Hive's own database file, through the connection every Entrance table
shares (``SqliteLink``). A failure is one upsert that increments in SQL and returns the new count,
so two failures at the same moment can never both read the same count and each write it plus one.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.logins``. Built by
    ``SqliteEntranceStore`` over its link. Calls into ``hivemind.common.sqlite`` and the
    protocol's rule.

Key invariants:
    - Every row names an existing device (foreign keys are on), so nothing is counted for a
      device the Entrance never enrolled.

See Also:
    - hivemind.entrance.store.logins.protocol for LoginTable.
    - hivemind.entrance.store.migrations for the 0002 tables.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import transaction
from hivemind.entrance.store.link import SqliteLink
from hivemind.entrance.store.logins.protocol import check_network
from waggle.ids import DeviceId

_COUNT_SQL = """
INSERT INTO entrance_login_failures (device_id, consecutive, last_failed_at) VALUES (?, 1, ?)
ON CONFLICT (device_id) DO UPDATE SET consecutive = consecutive + 1,
    last_failed_at = excluded.last_failed_at
RETURNING consecutive
"""
_FAILURES_SQL = "SELECT consecutive FROM entrance_login_failures WHERE device_id = ?"
_CLEAR_SQL = "DELETE FROM entrance_login_failures WHERE device_id = ?"
_NETWORKS_SQL = "SELECT network FROM entrance_device_networks WHERE device_id = ?"
_REMEMBER_SQL = """
INSERT INTO entrance_device_networks (device_id, network, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (device_id, network) DO UPDATE SET last_seen_at = excluded.last_seen_at
"""

__all__ = ["SqliteLoginTable"]


class SqliteLoginTable:
    """The durable LoginTable: two tables, written through the Entrance's one connection."""

    def __init__(self, link: SqliteLink) -> None:
        """Wrap the shared link; ``SqliteEntranceStore`` builds this once.

        Args:
            link: The Entrance tables' connection, thread and lock.
        """
        self._link = link

    async def count_failure(self, device_id: DeviceId, at: datetime) -> int:
        """Add one failure; see LoginTable.count_failure."""
        try:
            # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
            return await self._link.run(_count, device_id, at.isoformat())
        except sqlite3.IntegrityError as exc:
            raise _unknown(device_id) from exc

    async def failures(self, device_id: DeviceId) -> int:
        """Return the count; see LoginTable.failures."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        rows = await self._link.run(_read, _FAILURES_SQL, device_id)
        return int(rows[0]["consecutive"]) if rows else 0

    async def clear_failures(self, device_id: DeviceId) -> None:
        """Reset the count; see LoginTable.clear_failures."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        await self._link.run(_write, _CLEAR_SQL, (device_id,))

    async def networks(self, device_id: DeviceId) -> frozenset[str]:
        """Return the known networks; see LoginTable.networks."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        rows = await self._link.run(_read, _NETWORKS_SQL, device_id)
        return frozenset(str(row["network"]) for row in rows)

    async def remember_network(self, device_id: DeviceId, network: str, at: datetime) -> None:
        """Remember a network; see LoginTable.remember_network."""
        stamp = at.isoformat()
        params = (device_id, check_network(network), stamp, stamp)
        try:
            # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
            await self._link.run(_write, _REMEMBER_SQL, params)
        except sqlite3.IntegrityError as exc:
            raise _unknown(device_id) from exc


# ──────────────────────────────────────────────────────────────────────────────
# Blocking bodies: each runs on the link's connection thread, never on the event loop.
# ──────────────────────────────────────────────────────────────────────────────


def _count(connection: sqlite3.Connection, device_id: DeviceId, at: str) -> int:
    """Increment the device's count in SQL, in one transaction, and return the new value."""
    # fetchall, not fetchone: RETURNING leaves the statement running until it is drained, and
    # SQLite refuses to COMMIT under a write statement still in progress.
    with transaction(connection):
        (row,) = connection.execute(_COUNT_SQL, (device_id, at)).fetchall()
    return int(row["consecutive"])


def _read(connection: sqlite3.Connection, sql: str, device_id: DeviceId) -> list[sqlite3.Row]:
    """Run one read keyed by a device and return every row."""
    return connection.execute(sql, (device_id,)).fetchall()


def _write(connection: sqlite3.Connection, sql: str, params: tuple[str, ...]) -> None:
    """Run one write in its own transaction."""
    with transaction(connection):
        connection.execute(sql, params)


def _unknown(device_id: DeviceId) -> InvariantViolationError:
    """Name the one integrity failure these tables can have: a device never enrolled."""
    return InvariantViolationError(f"Device {device_id} is not enrolled.")
