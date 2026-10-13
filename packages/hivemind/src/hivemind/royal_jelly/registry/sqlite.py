"""Provide SqliteCombRegistry, the durable CombRegistry, and its migration entry point.

The Comb Registry's durable home is one table, `comb_tools`, in the Hive's single SQLite file
(ADR-0006): one row per (name, scope, Cell, version) of a promoted tool, with `is_current` flipped
inside the same transaction as the promote, rollback or retire that changes it. This class is the
`CombRegistry` over it: reads are single statements on the store's `ConnectionThread`, writes are
the three transactions in `registry.writes`, and the policy (who may write, what evidence is
needed) is `registry.rules`, shared with the in-memory fake. It records nothing on the Pheromone
Trail (the audit log): each write returns a `RegistryChange` for the caller to record, which is
also why `create` does not require the trail's table the way other stores do.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry.
    Constructed by a composition root (`hivemind.cli.stores`, added by a later wave) once the
    Hive Manifest names the database file. Calls into `hivemind.common` (connect, migrations),
    `registry.writes`, `registry.sql` and `registry.rules`.

Key invariants:
    - Every SQLite call runs on the store's `ConnectionThread`, serialised by this instance's
      `asyncio.Lock` (codingrules section 11).
    - The public surface is exactly the `CombRegistry` protocol's methods plus the `create`
      constructor; nothing else can write a row.
    - `promote` and `rollback` are the only methods that can leave a PROMOTED row with
      `is_current` True; `retire` can only clear it.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the decisions.
    - hivemind.cell.leavings.store_sqlite for the store shape this follows.
    - hivemind.honey_store.schema.migrate for the migration entry point shape.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread
from hivemind.royal_jelly.errors import ToolNotFoundError
from hivemind.royal_jelly.registry.principals import Principal
from hivemind.royal_jelly.registry.rows import CombToolRow, PromotionOffer, RegistryChange, ToolKey
from hivemind.royal_jelly.registry.rules import WriteContext, pick_visible, version_key
from hivemind.royal_jelly.registry.sql import (
    SELECT_ALL_SQL,
    SELECT_VISIBLE_SQL,
    values_to_row,
)
from hivemind.royal_jelly.registry.writes import (
    fetch_current,
    fetch_version,
    promote_transaction,
    retire_transaction,
    rollback_transaction,
)
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from waggle.clock import Clock
from waggle.ids import CellId
from waggle.messages.tool.authoring import ToolScope

SUBSYSTEM = "comb_registry"  # Keys this subsystem's rows in the shared schema_migrations table.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; a string,
# not a direct package import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.royal_jelly.registry.migrations"

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteCombRegistry",
    "apply_comb_registry_migrations",
]


def apply_comb_registry_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.royal_jelly.registry.migrations`.

    Synchronous, like every function `hivemind.common.migrations` exports; `SqliteCombRegistry.
    create` runs it under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending; empty when current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteCombRegistry:
    """The durable CombRegistry: one SQLite table, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `comb_tools`.
            clock: Injected clock; `promoted_at`, `retired_at` and the change instants come from it.
        """
        self._connection = connection
        self._clock = clock
        # One thread per connection: a cancelled await can never leave a transaction open under
        # the next caller's BEGIN (hivemind.common.sqlite.ConnectionThread).
        self._thread = ConnectionThread("hive-comb-registry")
        # Serialises every method on this instance, so a check and its write see one state.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteCombRegistry:
        """Apply this subsystem's migrations to `connection` and wrap it.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            clock: Injected clock, used for migration timestamps and every write's instant.

        Returns:
            A registry whose `comb_tools` table exists and is current.
        """
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_comb_registry_migrations, connection, clock)
        return cls(connection, clock)

    async def list_tools(
        self,
        scope: ToolScope | None = None,
        cell_id: CellId | None = None,
        *,
        current_only: bool = False,
    ) -> tuple[CombToolRow, ...]:
        """List stored versions; see CombRegistry.list_tools."""
        async with self._lock:
            # Blocking: one scan of a table that holds tools (hundreds of rows), sub-millisecond.
            records = await self._thread.run(
                lambda: self._connection.execute(SELECT_ALL_SQL).fetchall()
            )
        rows = (values_to_row(record) for record in records)
        # Filtered in Python: the table is small and a fixed statement keeps SQL assembled by hand
        # out of the module.
        wanted = [
            row
            for row in rows
            if (scope is None or row.scope is scope)
            and (cell_id is None or row.cell_id == cell_id)
            and (row.is_current or not current_only)
        ]
        wanted.sort(
            key=lambda r: (r.name, r.scope.value, str(r.cell_id or ""), version_key(r.version))
        )
        return tuple(wanted)

    async def get(
        self, name: str, scope: ToolScope, cell_id: CellId | None, version: str | None = None
    ) -> CombToolRow:
        """Return one version of a tool; see CombRegistry.get."""
        key = ToolKey(name, scope, cell_id)  # Validates that cell_id agrees with scope.
        async with self._lock:
            # Blocking: one indexed lookup on the unique identity index (or the partial current
            # one when no version is named).
            if version is None:
                row = await self._thread.run(fetch_current, self._connection, key)
            else:
                row = await self._thread.run(fetch_version, self._connection, key, version)
        if row is None:
            wanted = "a current version" if version is None else f"version {version}"
            raise ToolNotFoundError(f"Tool {name!r} at {scope.value} scope has no {wanted}.")
        return row

    async def visible_tools(self, cell_id: CellId) -> tuple[CombToolRow, ...]:
        """Return the current tools a Cell's bees may be offered; see CombRegistry."""
        async with self._lock:
            # Blocking: one scan over the current rows.
            records = await self._thread.run(
                lambda: self._connection.execute(SELECT_VISIBLE_SQL, (str(cell_id),)).fetchall()
            )
        return pick_visible(values_to_row(record) for record in records)

    async def promote(
        self,
        spec: ToolSpec,
        report: QuarantineReport,
        offer: PromotionOffer,
        principal: Principal,
        reason: str,
    ) -> RegistryChange:
        """Store a quarantined tool as its tool's new current version; see CombRegistry.promote.

        Records nothing on the Pheromone Trail: the caller records `tool.promoted` from the
        returned change.
        """
        context = WriteContext(principal, reason, self._clock.now())
        async with self._lock:
            # Blocking: reads and one INSERT in a single write transaction.
            return await self._thread.run(
                promote_transaction, self._connection, spec, report, offer, context
            )

    async def retire(
        self, key: ToolKey, version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Mark one version RETIRED; see CombRegistry.retire. Records nothing on the trail."""
        context = WriteContext(principal, reason, self._clock.now())
        async with self._lock:
            # Blocking: one read and one UPDATE in a single write transaction.
            return await self._thread.run(
                retire_transaction, self._connection, key, version, context
            )

    async def rollback(
        self, key: ToolKey, to_version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Make an older PROMOTED version current again; see CombRegistry.rollback."""
        context = WriteContext(principal, reason, self._clock.now())
        async with self._lock:
            # Blocking: reads and two UPDATEs in a single write transaction.
            return await self._thread.run(
                rollback_transaction, self._connection, key, to_version, context
            )
