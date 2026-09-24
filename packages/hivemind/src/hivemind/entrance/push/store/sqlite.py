"""Provide SqliteSubscriptionStore: push subscriptions and their delivery log in the Hive's file.

The durable ``hivemind.entrance.push.store.protocol.SubscriptionStore``. It lives in the same
database file as the rest of the Hive (``[hive] db``, ADR-0006) under its own migration series,
subsystem ``"entrance_push"`` (``hivemind.entrance.push.store.migrations``), so push subscriptions
move with the Entrance tables on Supersedure and survive a restart, when the dispatcher re-validates
them. Every operation is one hop to the store's own ``ConnectionThread`` (codingrules section 11),
and every write runs in one ``BEGIN IMMEDIATE`` transaction, so a check and the write that depends
on it see the same rows even against another process writing the same file.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.store``.
    Constructed by the Entrance's composition root once the manifest names the database. Calls
    into ``hivemind.common`` (the connection thread, transactions, migrations) and the push
    records.

Key invariants:
    - Every SQLite call runs on this store's ConnectionThread, serialised by its own lock.
    - A row's ``body`` is the source of truth; ``device_id``, ``channel``, ``endpoint`` and
      ``created_at`` are copies kept for uniqueness, filtering and ordering, written together.
    - Foreign keys are on (``hivemind.common.sqlite.connect``), so deleting a subscription deletes
      its delivery log rows in the same statement.

See Also:
    - hivemind.entrance.store.sqlite and hivemind.cell.leavings.store_sqlite for the pattern.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the one-file decision.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from collections.abc import Collection
from datetime import datetime

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.entrance.push.errors import SubscriptionExistsError
from hivemind.entrance.push.models import Subscription, SubscriptionId
from waggle.clock import Clock
from waggle.ids import DeviceId

SUBSYSTEM = "entrance_push"  # Keys this store's rows in the shared schema_migrations table.
# The dotted package importlib.resources reads the numbered .sql files from; a string, not an
# import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.entrance.push.store.migrations"

_TAKEN_SQL = """
SELECT 1 FROM entrance_push_subscriptions
WHERE id = ? OR (device_id = ? AND channel = ? AND endpoint = ?)
"""
_INSERT_SQL = """
INSERT INTO entrance_push_subscriptions (id, device_id, channel, endpoint, created_at, body)
VALUES (?, ?, ?, ?, ?, ?)
"""
_LIST_ALL_SQL = "SELECT body FROM entrance_push_subscriptions ORDER BY created_at, id"
_LIST_DEVICE_SQL = (
    "SELECT body FROM entrance_push_subscriptions WHERE device_id = ? ORDER BY created_at, id"
)
_DEVICE_IDS_SQL = (
    "SELECT id FROM entrance_push_subscriptions WHERE device_id = ? ORDER BY created_at, id"
)
_DELETE_SQL = "DELETE FROM entrance_push_subscriptions WHERE id = ?"
_DELETE_DEVICE_SQL = "DELETE FROM entrance_push_subscriptions WHERE device_id = ?"
# INSERT ... SELECT inserts nothing for an id no longer stored; OR IGNORE keeps the first time.
_RECORD_SQL = """
INSERT OR IGNORE INTO entrance_push_deliveries (ref, subscription_id, delivered_at)
SELECT ?, id, ? FROM entrance_push_subscriptions WHERE id = ?
"""
_RECIPIENTS_SQL = """
SELECT s.body FROM entrance_push_deliveries AS d
JOIN entrance_push_subscriptions AS s ON s.id = d.subscription_id
WHERE d.ref = ? ORDER BY s.created_at, s.id
"""
_FORGET_SQL = "DELETE FROM entrance_push_deliveries WHERE ref = ?"

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "SqliteSubscriptionStore", "apply_push_migrations"]


def apply_push_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under ``hivemind.entrance.push.store.migrations``.

    Synchronous, like every function in ``hivemind.common.migrations``;
    ``SqliteSubscriptionStore.create`` runs it off the event loop.

    Args:
        connection: An open connection from ``hivemind.common.sqlite.connect``.
        clock: Stamps each applied migration's ``applied_at``.

    Returns:
        The versions this call applied, ascending (empty once current).
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteSubscriptionStore:
    """The durable SubscriptionStore: two tables, one connection, one thread, one lock."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection; prefer ``create``.

        Args:
            connection: An open connection whose database already has the push tables.
        """
        self._connection = connection
        # One thread per connection: a cancelled await can never leave a transaction open under
        # the next caller's BEGIN (hivemind.common.sqlite.ConnectionThread).
        self._thread = ConnectionThread("hive-push")
        # Serialises this instance's operations, like every other SQLite store in the Hive.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteSubscriptionStore:
        """Apply the push migrations on ``connection`` and wrap it.

        Args:
            connection: An open connection, normally to the manifest's resolved ``[hive] db``.
            clock: Stamps migration records.

        Returns:
            A store whose tables exist and are current.
        """
        # Blocking: at most one transaction per pending migration, usually none once current.
        await asyncio.to_thread(apply_push_migrations, connection, clock)
        return cls(connection)

    async def add(self, subscription: Subscription) -> None:
        """Store a new subscription; see SubscriptionStore.add."""
        async with self._lock:
            # Blocking: one uniqueness check and one insert in one transaction.
            await self._thread.run(_insert, self._connection, subscription)

    async def list_for_device(self, device_id: DeviceId) -> tuple[Subscription, ...]:
        """Return one device's subscriptions; see SubscriptionStore.list_for_device."""
        return await self._read_subscriptions(_LIST_DEVICE_SQL, (device_id,))

    async def list_all(self) -> tuple[Subscription, ...]:
        """Return every subscription; see SubscriptionStore.list_all."""
        return await self._read_subscriptions(_LIST_ALL_SQL, ())

    async def delete(self, subscription_id: SubscriptionId) -> bool:
        """Delete one subscription and its log rows; see SubscriptionStore.delete."""
        async with self._lock:
            # Blocking: one delete; the foreign key cascades to the delivery log.
            deleted = await self._thread.run(_write, self._connection, _DELETE_SQL, subscription_id)
        return deleted > 0

    async def delete_for_device(self, device_id: DeviceId) -> tuple[SubscriptionId, ...]:
        """Delete one device's subscriptions; see SubscriptionStore.delete_for_device."""
        async with self._lock:
            # Blocking: one read and one delete in one transaction.
            return await self._thread.run(_delete_device, self._connection, device_id)

    async def record_delivery(
        self, ref: str, subscription_ids: Collection[SubscriptionId], at: datetime
    ) -> None:
        """Record a ref's recipients; see SubscriptionStore.record_delivery."""
        async with self._lock:
            # Blocking: one insert per recipient, all in one transaction.
            await self._thread.run(
                _record, self._connection, ref, tuple(subscription_ids), at.isoformat()
            )

    async def recipients(self, ref: str) -> tuple[Subscription, ...]:
        """Return a ref's recipients; see SubscriptionStore.recipients."""
        return await self._read_subscriptions(_RECIPIENTS_SQL, (ref,))

    async def forget_ref(self, ref: str) -> int:
        """Delete a ref's log rows; see SubscriptionStore.forget_ref."""
        async with self._lock:
            # Blocking: one delete over the log's primary key prefix.
            return await self._thread.run(_write, self._connection, _FORGET_SQL, ref)

    async def _read_subscriptions(
        self, sql: str, params: tuple[str, ...]
    ) -> tuple[Subscription, ...]:
        """Run one read of subscription bodies and decode them."""
        async with self._lock:
            # Blocking: one indexed read; a Hive holds a handful of subscriptions.
            rows = await self._thread.run(_fetch_all, self._connection, sql, params)
        return tuple(Subscription.model_validate_json(row["body"]) for row in rows)


# ──────────────────────────────────────────────────────────────────────────────
# Blocking bodies: each runs on the store's connection thread, never on the event loop.
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_all(
    connection: sqlite3.Connection, sql: str, params: tuple[str, ...]
) -> list[sqlite3.Row]:
    """Run one read and return every row."""
    return connection.execute(sql, params).fetchall()


def _write(connection: sqlite3.Connection, sql: str, key: str) -> int:
    """Run one keyed write in its own transaction and return how many rows it touched."""
    with transaction(connection):
        return connection.execute(sql, (key,)).rowcount


def _insert(connection: sqlite3.Connection, subscription: Subscription) -> None:
    """Insert a subscription unless its id or its destination is taken, in one transaction."""
    destination = (subscription.device_id, subscription.channel.value, subscription.endpoint)
    with transaction(connection):
        if connection.execute(_TAKEN_SQL, (subscription.id, *destination)).fetchone() is not None:
            raise SubscriptionExistsError(subscription.id, subscription.device_id)
        connection.execute(
            _INSERT_SQL,
            (
                subscription.id,
                *destination,
                subscription.created_at.isoformat(),
                subscription.model_dump_json(),
            ),
        )


def _delete_device(
    connection: sqlite3.Connection, device_id: DeviceId
) -> tuple[SubscriptionId, ...]:
    """Read one device's subscription ids and delete them, in one transaction."""
    with transaction(connection):
        rows = connection.execute(_DEVICE_IDS_SQL, (device_id,)).fetchall()
        connection.execute(_DELETE_DEVICE_SQL, (device_id,))
    return tuple(SubscriptionId(row["id"]) for row in rows)


def _record(
    connection: sqlite3.Connection, ref: str, subscription_ids: tuple[str, ...], at: str
) -> None:
    """Insert one delivery log row per stored recipient, in one transaction."""
    with transaction(connection):
        for subscription_id in subscription_ids:
            connection.execute(_RECORD_SQL, (ref, at, subscription_id))
