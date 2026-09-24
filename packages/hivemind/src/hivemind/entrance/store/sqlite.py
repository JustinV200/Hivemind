"""Provide SqliteEntranceStore: the Entrance tables in the Hive's own SQLite file.

The durable ``hivemind.entrance.store.protocol.EntranceStore``. It lives in the same database file
as the rest of the Hive (``[hive] db``, ADR-0006) with its own migration series, subsystem
``"entrance"`` (``hivemind.entrance.store.migrations``), so the Entrance's (the Hive's one HTTP
door) state moves with the other stores on Supersedure (moving the Hive Stand, the Queen's machine,
elsewhere). Every operation is one hop to the store's own ``ConnectionThread`` (codingrules section
11); every write runs in one ``BEGIN IMMEDIATE`` transaction that re-reads the row it guards,
applies the protocol's rule (``check_new_device``, ``transition_device``, ``use_invite``) and writes
the result, so the state machine and the single-use rule see the row as it is, even against another
process writing the same file.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Constructed by
    a composition root (the Entrance app, a CLI command) once the manifest names the database.
    Calls into ``hivemind.common`` (connect, transaction, migrations, the connection thread),
    ``hivemind.entrance.enrol`` and ``hivemind.entrance.errors``.

Key invariants:
    - Every SQLite call runs on this store's ConnectionThread, serialised by its own lock.
    - A row's ``body`` (the record's JSON) is the source of truth; ``status`` and ``created_at``
      are copies kept for filtering and ordering, written in the same statement as the body.
    - The store needs no Pheromone Trail table yet: it writes no events until the behaviour half
      of roadmap 10.5d adds them (``hivemind.entrance.store.protocol``'s docstring).

See Also:
    - hivemind.cell.leavings.store_sqlite and hivemind.brood_chamber.store.sqlite for the pattern.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the one-file decision.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import datetime
from typing import Unpack

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.entrance.enrol.models import DeviceInvite, EnrolledDevice, OperatorCredential
from hivemind.entrance.enrol.state import DeviceStatus
from hivemind.entrance.errors import (
    DeviceAlreadyExistsError,
    DeviceNotFoundError,
    DeviceStatusConflictError,
    InviteAlreadyExistsError,
    InviteNotFoundError,
)
from hivemind.entrance.store.protocol import (
    DeviceChanges,
    check_new_device,
    transition_device,
    use_invite,
)
from waggle.clock import Clock
from waggle.ids import DeviceId

SUBSYSTEM = "entrance"  # Keys this store's rows in the shared schema_migrations table.
# The dotted package importlib.resources reads the numbered .sql files from; a string, not an
# import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.entrance.store.migrations"

_SELECT_OPERATOR_SQL = "SELECT password_hash, created_at, changed_at FROM entrance_operator"
_UPSERT_OPERATOR_SQL = """
INSERT INTO entrance_operator (id, password_hash, created_at, changed_at) VALUES (1, ?, ?, ?)
ON CONFLICT (id) DO UPDATE SET password_hash = excluded.password_hash,
    changed_at = excluded.changed_at
"""
_SELECT_DEVICE_SQL = "SELECT body FROM entrance_devices WHERE id = ?"
_INSERT_DEVICE_SQL = (
    "INSERT INTO entrance_devices (id, status, created_at, body) VALUES (?, ?, ?, ?)"
)
_UPDATE_DEVICE_SQL = "UPDATE entrance_devices SET status = ?, body = ? WHERE id = ?"
_LIST_DEVICES_SQL = "SELECT body FROM entrance_devices ORDER BY created_at, id"
_LIST_DEVICES_BY_STATUS_SQL = (
    "SELECT body FROM entrance_devices WHERE status = ? ORDER BY created_at, id"
)
_SELECT_INVITE_SQL = "SELECT body FROM entrance_invites WHERE code_hash = ?"
_INVITE_TAKEN_SQL = "SELECT 1 FROM entrance_invites WHERE code_hash = ? OR device_id = ?"
_INSERT_INVITE_SQL = "INSERT INTO entrance_invites (code_hash, device_id, body) VALUES (?, ?, ?)"
_UPDATE_INVITE_SQL = "UPDATE entrance_invites SET body = ? WHERE code_hash = ?"

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "SqliteEntranceStore", "apply_entrance_migrations"]


def apply_entrance_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under ``hivemind.entrance.store.migrations``.

    Synchronous, like every function in ``hivemind.common.migrations``; ``SqliteEntranceStore.
    create`` runs it under ``asyncio.to_thread``.

    Args:
        connection: An open connection from ``hivemind.common.sqlite.connect``.
        clock: Stamps each applied migration's ``applied_at``.

    Returns:
        The versions this call applied, ascending (empty once current).
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteEntranceStore:
    """The durable EntranceStore: three tables, one connection, one thread, one lock."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection; prefer ``create``.

        Args:
            connection: An open connection whose database already has the Entrance tables.
        """
        self._connection = connection
        # One thread per connection: a cancelled await can never leave a transaction open under
        # the next caller's BEGIN (hivemind.common.sqlite.ConnectionThread).
        self._thread = ConnectionThread("hive-entrance")
        # Serialises this instance's operations, like every other SQLite store in the Hive.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteEntranceStore:
        """Apply the Entrance's migrations on ``connection`` and wrap it.

        Args:
            connection: An open connection from ``hivemind.common.sqlite.connect``, normally to
                the manifest's resolved ``[hive] db``.
            clock: Stamps migration records.

        Returns:
            A store whose tables exist and are current.
        """
        # Blocking: at most one transaction per pending migration, usually none once current.
        await asyncio.to_thread(apply_entrance_migrations, connection, clock)
        return cls(connection)

    async def get_operator(self) -> OperatorCredential | None:
        """Return the operator row; see EntranceStore.get_operator."""
        async with self._lock:
            # Blocking, sub-millisecond: one primary-key read (busy_timeout bounds any lock wait).
            row = await self._thread.run(_fetch_one, self._connection, _SELECT_OPERATOR_SQL, ())
        if row is None:
            return None
        return OperatorCredential(
            password_hash=row["password_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            changed_at=datetime.fromisoformat(row["changed_at"]),
        )

    async def set_operator_password_hash(self, password_hash: str, at: datetime) -> None:
        """Create or change the operator row; see EntranceStore.set_operator_password_hash."""
        async with self._lock:
            # Blocking: one read and one upsert in one transaction.
            await self._thread.run(_set_operator, self._connection, password_hash, at)

    async def put_device(self, device: EnrolledDevice) -> None:
        """Record a new device at its entry; see EntranceStore.put_device."""
        check_new_device(device)
        async with self._lock:
            # Blocking: one existence check and one insert in one transaction.
            await self._thread.run(_insert_device, self._connection, device)

    async def get_device(self, device_id: DeviceId) -> EnrolledDevice:
        """Return one device; see EntranceStore.get_device."""
        async with self._lock:
            # Blocking, sub-millisecond: one primary-key read.
            row = await self._thread.run(
                _fetch_one, self._connection, _SELECT_DEVICE_SQL, (device_id,)
            )
        if row is None:
            raise DeviceNotFoundError(device_id)
        return EnrolledDevice.model_validate_json(row["body"])

    async def list_devices(self, status: DeviceStatus | None = None) -> tuple[EnrolledDevice, ...]:
        """Return devices, oldest first; see EntranceStore.list_devices."""
        async with self._lock:
            # Blocking: one indexed scan; a Hive holds a handful of devices, never thousands.
            if status is None:
                rows = await self._thread.run(_fetch_all, self._connection, _LIST_DEVICES_SQL, ())
            else:
                by_status = (_LIST_DEVICES_BY_STATUS_SQL, (status.value,))
                rows = await self._thread.run(_fetch_all, self._connection, *by_status)
        return tuple(EnrolledDevice.model_validate_json(row["body"]) for row in rows)

    async def update_device_status(
        self,
        device_id: DeviceId,
        expected: DeviceStatus,
        new: DeviceStatus,
        **changes: Unpack[DeviceChanges],
    ) -> EnrolledDevice:
        """Move a device along the state machine; see EntranceStore.update_device_status."""
        async with self._lock:
            # Blocking: one read and one write in one transaction.
            return await self._thread.run(
                _transition, self._connection, device_id, expected, new, changes
            )

    async def put_invite(self, invite: DeviceInvite) -> None:
        """Record an invite for an INVITED device; see EntranceStore.put_invite."""
        async with self._lock:
            # Blocking: three checks and one insert in one transaction.
            await self._thread.run(_insert_invite, self._connection, invite)

    async def get_invite(self, code_hash: str) -> DeviceInvite:
        """Return one invite; see EntranceStore.get_invite."""
        async with self._lock:
            # Blocking, sub-millisecond: one primary-key read.
            row = await self._thread.run(
                _fetch_one, self._connection, _SELECT_INVITE_SQL, (code_hash,)
            )
        if row is None:
            raise InviteNotFoundError(code_hash)
        return DeviceInvite.model_validate_json(row["body"])

    async def mark_invite_used(self, code_hash: str, used_at: datetime) -> DeviceInvite:
        """Spend an invite once; see EntranceStore.mark_invite_used."""
        async with self._lock:
            # Blocking: one read and one write in one transaction.
            return await self._thread.run(_use_invite, self._connection, code_hash, used_at)


# ──────────────────────────────────────────────────────────────────────────────
# Blocking bodies: each runs on the store's connection thread, never on the event loop.
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_one(
    connection: sqlite3.Connection, sql: str, params: tuple[str, ...]
) -> sqlite3.Row | None:
    """Run one read and return its first row."""
    row: sqlite3.Row | None = connection.execute(sql, params).fetchone()
    return row


def _fetch_all(
    connection: sqlite3.Connection, sql: str, params: tuple[str, ...]
) -> list[sqlite3.Row]:
    """Run one read and return every row."""
    return connection.execute(sql, params).fetchall()


def _set_operator(connection: sqlite3.Connection, password_hash: str, at: datetime) -> None:
    """Validate the new row against the stored one, then create or change it."""
    with transaction(connection):
        row = connection.execute(_SELECT_OPERATOR_SQL).fetchone()
        created_at = datetime.fromisoformat(row["created_at"]) if row is not None else at
        # Built before the write: only an Argon2id PHC string, changed no earlier than it
        # was created, may ever reach the table (the same rule MemoryEntranceStore applies).
        credential = OperatorCredential(
            password_hash=password_hash, created_at=created_at, changed_at=at
        )
        connection.execute(
            _UPSERT_OPERATOR_SQL,
            (
                credential.password_hash,
                credential.created_at.isoformat(),
                credential.changed_at.isoformat(),
            ),
        )


def _insert_device(connection: sqlite3.Connection, device: EnrolledDevice) -> None:
    """Insert a new device row unless its id is taken, in one transaction."""
    with transaction(connection):
        if connection.execute(_SELECT_DEVICE_SQL, (device.id,)).fetchone() is not None:
            raise DeviceAlreadyExistsError(device.id)
        connection.execute(
            _INSERT_DEVICE_SQL,
            (device.id, device.status.value, device.created_at.isoformat(), _body(device)),
        )


def _transition(
    connection: sqlite3.Connection,
    device_id: DeviceId,
    expected: DeviceStatus,
    new: DeviceStatus,
    changes: DeviceChanges,
) -> EnrolledDevice:
    """Re-read the device, apply the one transition rule, and write the result."""
    with transaction(connection):
        row = connection.execute(_SELECT_DEVICE_SQL, (device_id,)).fetchone()
        if row is None:
            raise DeviceNotFoundError(device_id)
        current = EnrolledDevice.model_validate_json(row["body"])
        updated = transition_device(current, expected, new, changes)
        connection.execute(_UPDATE_DEVICE_SQL, (updated.status.value, _body(updated), device_id))
        return updated


def _insert_invite(connection: sqlite3.Connection, invite: DeviceInvite) -> None:
    """Insert an invite for an existing INVITED device, checking in the protocol's order."""
    with transaction(connection):
        row = connection.execute(_SELECT_DEVICE_SQL, (invite.device_id,)).fetchone()
        if row is None:
            raise DeviceNotFoundError(invite.device_id)
        taken = connection.execute(
            _INVITE_TAKEN_SQL, (invite.code_hash, invite.device_id)
        ).fetchone()
        if taken is not None:
            raise InviteAlreadyExistsError(
                f"An invite with code hash {invite.code_hash[:12]}... or for device "
                f"{invite.device_id} already exists."
            )
        device = EnrolledDevice.model_validate_json(row["body"])
        if device.status is not DeviceStatus.INVITED:
            raise DeviceStatusConflictError(device.id, DeviceStatus.INVITED, device.status)
        connection.execute(_INSERT_INVITE_SQL, (invite.code_hash, invite.device_id, _body(invite)))


def _use_invite(connection: sqlite3.Connection, code_hash: str, used_at: datetime) -> DeviceInvite:
    """Re-read the invite, apply the single-use rule, and write the result."""
    with transaction(connection):
        row = connection.execute(_SELECT_INVITE_SQL, (code_hash,)).fetchone()
        if row is None:
            raise InviteNotFoundError(code_hash)
        used = use_invite(DeviceInvite.model_validate_json(row["body"]), used_at)
        connection.execute(_UPDATE_INVITE_SQL, (_body(used), code_hash))
        return used


def _body(record: EnrolledDevice | DeviceInvite) -> str:
    """Serialise a record as the JSON body its row stores."""
    return record.model_dump_json()
