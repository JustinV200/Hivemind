"""Provide SqliteSwarmRegistry: the swarm registry in the Hive's own SQLite file.

The durable `SwarmRegistry`. It lives in the same database file as the rest of the Hive
(`[hive] db`, ADR-0006) with its own migration series, subsystem "swarm"
(`hivemind.swarm.registry.migrations`), so the registry moves with the other stores on
Supersedure (moving the Hive Stand to another machine). Every operation is one hop to the
store's own `ConnectionThread` (codingrules section 11); every write runs in one `BEGIN IMMEDIATE`
transaction that re-reads the row it guards, applies the rule from `hivemind.swarm.registry.rules`
and writes the result together with its trail event (`hivemind.pheromone.insert_event`, on this
store's own connection to the same file), so a redemption can never be half done: the node, the
spent invite and `swarm.enrolled` commit together or not at all, and two devices presenting one
token cannot both win, even from two processes writing the same file.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.registry`.
    Constructed by a composition root (`hivemind.cli.stores.open_swarm_registry`) once the
    manifest names the database. Calls into hivemind.common (connect, transaction, migrations),
    hivemind.pheromone (insert_event), hivemind.swarm and the standard library only.

Key invariants:
    - `create` refuses a file without the Pheromone Trail's `pheromone_events` table, because
      every event handed to a write is inserted there in the same transaction.
    - Every SQLite call runs on this store's `ConnectionThread`, serialised by its own lock.
    - A row's `body` (the model's JSON) is the source of truth; the plain columns are copies
      written in the same statement.
    - A refused redemption of a lapsed invite still commits the invite's move to EXPIRED.

See Also:
    - hivemind.cell.leavings.store_sqlite and hivemind.entrance.store.sqlite for the pattern.
    - hivemind.swarm.registry.memory for the in-memory implementation this one matches.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the one-file decision.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from collections.abc import Callable
from datetime import datetime

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel
from hivemind.common.errors import MigrationError
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.forage import ForageCapacity
from hivemind.pheromone import SwarmEvent, insert_event
from hivemind.swarm.errors import (
    EnrolmentDeniedError,
    EnrolRefusal,
    InviteConflictError,
    InviteNotFoundError,
    NodeNotFoundError,
)
from hivemind.swarm.models import InviteState, SwarmInvite, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import rows, rules
from waggle.clock import Clock
from waggle.ids import CellId, DeviceId, NodeId
from waggle.messages.swarm import RuntimeLevel

SUBSYSTEM = "swarm"  # Keys this registry's rows in the shared schema_migrations table.
# The dotted package importlib.resources reads the numbered .sql files from; a string, not an
# import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.swarm.registry.migrations"

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "SqliteSwarmRegistry", "apply_swarm_migrations"]


def apply_swarm_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.swarm.registry.migrations`.

    Synchronous, like every function in `hivemind.common.migrations`; `SqliteSwarmRegistry.create`
    runs it under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Stamps each applied migration's `applied_at`.

    Returns:
        The migration versions actually applied by this call, ascending.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteSwarmRegistry:
    """The durable SwarmRegistry: two tables, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has the swarm tables.
        """
        self._connection = connection
        # One thread per connection: a cancelled await can never leave a transaction open under
        # the next caller's BEGIN (hivemind.common.sqlite.ConnectionThread).
        self._thread = ConnectionThread("hive-swarm")
        # Serialises every method on this instance, as the other stores' own locks do.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteSwarmRegistry:
        """Check for the Pheromone Trail's table, apply this subsystem's migrations, and wrap.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`. A composition
                root applies the Pheromone Trail's own migrations on its connection to the same
                file before calling this.
            clock: Injected clock, used for migration timestamps.

        Returns:
            A SqliteSwarmRegistry whose tables exist and are current.

        Raises:
            MigrationError: `connection`'s database has no `pheromone_events` table yet.
        """
        # Blocking: one indexed lookup in sqlite_master; sub-millisecond.
        has_trail = await asyncio.to_thread(
            lambda: connection.execute(rows.PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None
        )
        if not has_trail:
            raise MigrationError(
                "cannot apply swarm migrations: no pheromone_events table on this connection; "
                "call hivemind.pheromone.trail.sqlite.SqlitePheromoneTrail.create on this "
                "database file first."
            )
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_swarm_migrations, connection, clock)
        return cls(connection)

    async def put_invite(self, invite: SwarmInvite, event: SwarmEvent | None = None) -> None:
        """Store a freshly minted invite; see `SwarmRegistry.put_invite`."""
        if invite.state is not InviteState.ISSUED:
            raise InviteConflictError(f"A new invite must be ISSUED, got {invite.state.name}.")
        await self._write(_insert_invite, invite, event)

    async def get_invite(self, code_hash: str) -> SwarmInvite | None:
        """Return the invite with this token hash, or None."""
        found = await self._read_one(rows.SELECT_INVITE_SQL, code_hash)
        return None if found is None else rows.invite_from_row(found)

    async def invites(self, state: InviteState | None = None) -> tuple[SwarmInvite, ...]:
        """List invites, oldest first; see `SwarmRegistry.invites`."""
        if state is None:
            found = await self._read_all(rows.LIST_INVITES_SQL)
        else:
            found = await self._read_all(rows.LIST_INVITES_BY_STATE_SQL, state.value)
        return tuple(rows.invite_from_row(row) for row in found)

    async def expire_invites(self, at: datetime) -> int:
        """Move every lapsed ISSUED invite to EXPIRED; see `SwarmRegistry.expire_invites`."""
        return await self._write(_expire_invites, at)

    async def redeem(
        self, code_hash: str, node: SwarmNode, at: datetime, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Spend an invite and create its node atomically; see `SwarmRegistry.redeem`."""
        refusal = await self._write(_redeem, code_hash, node, at, event)
        if refusal is not None:
            raise EnrolmentDeniedError(refusal, node.device_id, node.node_id)
        return node

    async def revoke_invite(self, code_hash: str, event: SwarmEvent | None = None) -> SwarmInvite:
        """Cancel an unused invite; see `SwarmRegistry.revoke_invite`."""
        return await self._write(_revoke_invite, code_hash, event)

    async def node(self, device_id: DeviceId) -> SwarmNode | None:
        """Return the enrolled device with this id, or None."""
        found = await self._read_one(rows.SELECT_NODE_SQL, device_id)
        return None if found is None else rows.node_from_row(found)

    async def node_by_node_id(self, node_id: NodeId) -> SwarmNode | None:
        """Return the enrolled device whose frames this node id signs, or None."""
        found = await self._read_one(rows.SELECT_NODE_BY_NODE_ID_SQL, node_id)
        return None if found is None else rows.node_from_row(found)

    async def node_by_cell(self, cell_id: CellId) -> SwarmNode | None:
        """Return the enrolled device that is this Real Cell, or None."""
        found = await self._read_one(rows.SELECT_NODE_BY_CELL_SQL, cell_id)
        return None if found is None else rows.node_from_row(found)

    async def nodes(self, status: NodeStatus | None = None) -> tuple[SwarmNode, ...]:
        """List enrolled devices, oldest enrolment first."""
        if status is None:
            found = await self._read_all(rows.LIST_NODES_SQL)
        else:
            found = await self._read_all(rows.LIST_NODES_BY_STATUS_SQL, status.value)
        return tuple(rows.node_from_row(row) for row in found)

    async def set_status(
        self,
        device_id: DeviceId,
        expected: NodeStatus,
        new: NodeStatus,
        at: datetime,
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Move a node's status, a compare-and-set; see `SwarmRegistry.set_status`."""
        return await self._change(
            device_id, lambda node: rules.with_status(node, expected, new, at), event
        )

    async def set_level(
        self, device_id: DeviceId, level: RuntimeLevel, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Move a node one rung along the ladder; see `SwarmRegistry.set_level`."""
        return await self._change(device_id, lambda node: rules.with_level(node, level), event)

    async def set_desired_level(self, device_id: DeviceId, level: RuntimeLevel | None) -> SwarmNode:
        """Record the operator's pending rung request; see `SwarmRegistry.set_desired_level`."""
        return await self._change(
            device_id, lambda node: rules.with_desired_level(node, level), None
        )

    async def set_access(
        self,
        device_id: DeviceId,
        level: AccessLevel,
        granted: tuple[str, ...],
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Change a node's access level; see `SwarmRegistry.set_access`."""
        return await self._change(
            device_id, lambda node: rules.with_access(node, level, granted), event
        )

    async def set_shield(
        self, device_id: DeviceId, shield: CombShieldLevel, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Change a node's Comb Shield tier; see `SwarmRegistry.set_shield`."""
        return await self._change(device_id, lambda node: rules.with_shield(node, shield), event)

    async def touch(
        self, device_id: DeviceId, at: datetime, runtime_version: str | None = None
    ) -> None:
        """Note that a verified frame arrived from the device; see `SwarmRegistry.touch`."""
        await self._change(device_id, lambda node: rules.touched(node, at, runtime_version), None)

    async def update_facts(
        self, device_id: DeviceId, capabilities: CellCapabilities, capacity: ForageCapacity
    ) -> SwarmNode:
        """Store the latest capability and capacity report; see `SwarmRegistry.update_facts`."""
        return await self._change(
            device_id, lambda node: rules.with_facts(node, capabilities, capacity), None
        )

    async def _change(
        self,
        device_id: DeviceId,
        change: Callable[[SwarmNode], SwarmNode],
        event: SwarmEvent | None,
    ) -> SwarmNode:
        """Apply one pure change to a stored node in one transaction, with its event."""
        return await self._write(_change_node, device_id, change, event)

    async def _write[ResultT](self, fn: Callable[..., ResultT], /, *args: object) -> ResultT:
        """Run one transaction function on the connection's thread, serialised by the lock."""
        async with self._lock:
            # Blocking: one BEGIN IMMEDIATE transaction of a few statements, local disk.
            return await self._thread.run(fn, self._connection, *args)

    async def _read_one(self, sql: str, key: str) -> sqlite3.Row | None:
        """Fetch at most one row by a single key."""
        async with self._lock:
            return await self._thread.run(lambda: self._connection.execute(sql, (key,)).fetchone())

    async def _read_all(self, sql: str, *params: str) -> list[sqlite3.Row]:
        """Fetch every row a statement matches."""
        async with self._lock:
            return await self._thread.run(lambda: self._connection.execute(sql, params).fetchall())


# ──────────────────────────────────────────────────────────────────────────────
# Transaction bodies: synchronous, run on the connection's own thread
# ──────────────────────────────────────────────────────────────────────────────


def _record(connection: sqlite3.Connection, event: SwarmEvent | None) -> None:
    """Insert `event` into the Pheromone Trail's table inside the caller's transaction."""
    if event is not None:
        insert_event(connection, event)


def _insert_invite(
    connection: sqlite3.Connection, invite: SwarmInvite, event: SwarmEvent | None
) -> None:
    """Insert a new invite and its event, refusing a token hash that is already stored."""
    with transaction(connection):
        if connection.execute(rows.SELECT_INVITE_SQL, (invite.code_hash,)).fetchone() is not None:
            raise InviteConflictError(
                f"An invite with the token hash {invite.code_hash[:12]}... already exists."
            )
        connection.execute(rows.INSERT_INVITE_SQL, rows.invite_insert_values(invite))
        _record(connection, event)


def _store_invite(connection: sqlite3.Connection, invite: SwarmInvite) -> None:
    """Write an invite's new state, device and body over its row."""
    connection.execute(rows.UPDATE_INVITE_SQL, rows.invite_update_values(invite))


def _expire_invites(connection: sqlite3.Connection, at: datetime) -> int:
    """Move every lapsed ISSUED invite to EXPIRED in one transaction; return how many."""
    with transaction(connection):
        lapsed = connection.execute(rows.LAPSED_INVITES_SQL, (at.isoformat(),)).fetchall()
        for row in lapsed:
            _store_invite(connection, rules.expired_invite(rows.invite_from_row(row)))
        return len(lapsed)


def _redeem(
    connection: sqlite3.Connection,
    code_hash: str,
    node: SwarmNode,
    at: datetime,
    event: SwarmEvent | None,
) -> EnrolRefusal | None:
    """Spend an invite and insert its node in one transaction; return a refusal or None."""
    with transaction(connection):
        found = connection.execute(rows.SELECT_INVITE_SQL, (code_hash,)).fetchone()
        invite = None if found is None else rows.invite_from_row(found)
        refusal = rules.judge_redemption(invite, at)
        # A lapsed invite is moved to EXPIRED and that move commits even though this redemption
        # is refused, so the registry's record is true; returning (not raising) is what commits.
        if (
            invite is not None
            and refusal is EnrolRefusal.EXPIRED
            and invite.state is InviteState.ISSUED
        ):
            _store_invite(connection, rules.expired_invite(invite))
        if refusal is None and _identity_in_use(connection, node):
            refusal = EnrolRefusal.IDENTITY_IN_USE
        # `invite is None` always comes with a refusal; the test only narrows the type.
        if invite is None or refusal is not None:
            return refusal or EnrolRefusal.UNKNOWN_INVITE
        connection.execute(rows.INSERT_NODE_SQL, rows.node_insert_values(node))
        _store_invite(connection, rules.redeemed_invite(invite, node.device_id))
        _record(connection, event)
        return None


def _identity_in_use(connection: sqlite3.Connection, node: SwarmNode) -> bool:
    """Return whether an enrolled node already owns one of `node`'s five unique identities."""
    found = connection.execute(rows.NODE_IDENTITY_IN_USE_SQL, rows.node_identity_values(node))
    return found.fetchone() is not None


def _revoke_invite(
    connection: sqlite3.Connection, code_hash: str, event: SwarmEvent | None
) -> SwarmInvite:
    """Move an ISSUED invite to REVOKED in one transaction with its event."""
    with transaction(connection):
        found = connection.execute(rows.SELECT_INVITE_SQL, (code_hash,)).fetchone()
        if found is None:
            raise InviteNotFoundError(code_hash)
        revoked = rules.revoked_invite(rows.invite_from_row(found))
        _store_invite(connection, revoked)
        _record(connection, event)
        return revoked


def _change_node(
    connection: sqlite3.Connection,
    device_id: DeviceId,
    change: Callable[[SwarmNode], SwarmNode],
    event: SwarmEvent | None,
) -> SwarmNode:
    """Re-read a node, apply one pure change and write it back with its event, atomically."""
    with transaction(connection):
        found = connection.execute(rows.SELECT_NODE_SQL, (device_id,)).fetchone()
        if found is None:
            raise NodeNotFoundError(device_id)
        updated = change(rows.node_from_row(found))
        connection.execute(rows.UPDATE_NODE_SQL, rows.node_update_values(updated))
        _record(connection, event)
        return updated
