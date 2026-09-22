"""Define ClusterOrder and OrderStore: the durable rows `hive cluster`/`hive wake` write.

docs/adr/0024-clustering-protocol.md's second decision: `hive cluster [provider]` and `hive wake`
run in a second process from the running Queen (`hive run`, roadmap step 3.21), so an operator
order cannot reach her through a direct call -- it is written as a durable row in a small table she
polls every tick, the same shape `hivemind.queen.questions.sync_answers_from_chamber` already uses
for `hive inbox answer`. `ClusterOrder` is one such row: `CLUSTER` (optionally naming one
provider; naming none means every currently-bound provider) or `WAKE` (optionally naming one
provider; naming none means every currently clustered provider), `requested_at` when it was
written, `handled_at` once `hivemind.queen.cluster.tick.run_cluster_tick` has acted on it.
`OrderStore` is the persistence seam, mirroring `hivemind.queen.forage.ledger.store_protocol.
LedgerStore`'s own Protocol-plus-two-implementations shape: `InMemoryOrderStore` for tests and a
Queen with no database file yet, `SqliteOrderStore` (with its own numbered `.sql` migration
beside it, `queen/forage/ledger/store_sqlite.py`'s own precedent for why the file sits here rather
than under a `migrations/` sub-package -- `queen/cluster/` already sits three levels below
`src/hivemind`, codingrules section 3's own depth limit) as the durable implementation `hive
cluster`/`hive wake` (roadmap step 4.11, a separate dispatch) and the running Queen's own tick
share through one open connection to the Hive's `[hive] db` file.

Roadmap step 5.13 (`hive cells release <lease-id>`) reuses this same table and store rather than
building a sibling `queen/orders/` package (the roadmap step's own "choose the smaller change"):
`OrderKind.RELEASE` is a third order kind, naming a `lease_id` instead of a `provider` (the
`0002_add_release_lease_id.sql` migration adds that one nullable column). `provider` and
`lease_id` are never both set on one order: a `CLUSTER`/`WAKE` row only ever names a provider, a
`RELEASE` row only ever names a lease. `hivemind.queen.cluster.tick.run_release_tick` is the
RELEASE-only counterpart to `run_cluster_tick`'s own CLUSTER/WAKE draining, so `_drain_orders`
there filters to `{CLUSTER, WAKE}` explicitly rather than treating "not CLUSTER" as "WAKE" the way
it could when only two kinds existed.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. Written by whichever composition root wires `hive cluster`/`hive wake` (roadmap
    step 4.11, not this dispatch); read every tick by `hivemind.queen.cluster.tick.
    run_cluster_tick`. Calls into `hivemind.common` (connect, transaction, migrations) and waggle
    only.

Key invariants:
    - `mark_handled` is idempotent: marking an already-handled or unknown order id a second time
      is a no-op, matching `hivemind.queen.forage.ledger.store_protocol.LedgerStore.delete_grant`'s
      own idempotent-delete contract.
    - `pending()` never returns an order whose `handled_at` is already set; `mark_handled` is the
      only way an order leaves `pending()`'s own result, so a WAKE row is consumed exactly once
      (`run_cluster_tick` marks it handled in the same call that acts on it, before its next poll
      could see it again).
    - Every `put_order`/`pending`/`mark_handled` call on `SqliteOrderStore` runs under
      the store's own `ConnectionThread`, serialised by one `asyncio.Lock` per instance, mirroring
      `hivemind.queen.forage.ledger.store_sqlite.SqliteLedgerStore`'s own contract.

See Also:
    - docs/adr/0024-clustering-protocol.md for the decision this module implements.
    - hivemind.queen.questions for sync_answers_from_chamber, the durable-row-polled-each-tick
      precedent this module's own shape follows.
    - hivemind.queen.forage.ledger.store_protocol for LedgerStore, the Protocol-plus-two-
      implementations pattern this module mirrors.
    - hivemind.queen.cluster.tick for run_cluster_tick, this module's one reader.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread
from waggle.clock import Clock
from waggle.ids import IdKind, new_id

SUBSYSTEM = "queen_cluster_orders"  # Keys this subsystem's rows in the shared schema_migrations.
MIGRATIONS_PACKAGE = "hivemind.queen.cluster"  # Where the numbered .sql file beside this one sits.

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "ClusterOrder",
    "InMemoryOrderStore",
    "OrderKind",
    "OrderStore",
    "SqliteOrderStore",
    "apply_order_migrations",
    "new_order_id",
]


class OrderKind(Enum):
    """What an operator order asks the running Queen to do."""

    CLUSTER = "CLUSTER"  # Pause: one named provider, or every currently-bound one if none named.
    WAKE = "WAKE"  # Resume: one named provider, or every currently clustered one if none named.
    # Roadmap step 5.13: release one named Real Cell lease (hive cells release). Always names a
    # lease_id, never a provider -- see this module's own docstring.
    RELEASE = "RELEASE"


@dataclass(frozen=True, slots=True)
class ClusterOrder:
    """One durable `hive cluster`/`hive wake`/`hive cells release` row the running Queen polls for.

    Attributes:
        id: This order's own id (module docstring: waggle's closed `IdKind` set has no dedicated
            kind for an operator order, so this reuses `IdKind.EVENT`'s own ULID minting rather
            than widening that shared enum for one small table this dispatch owns).
        kind: CLUSTER, WAKE or RELEASE.
        provider: The `[llm.providers.<name>]` key this order names; None means "every provider"
            (module docstring: every currently-bound one for CLUSTER, every currently clustered
            one for WAKE); always None for a RELEASE order.
        lease_id: The `LeaseId` a RELEASE order names; always None for a CLUSTER/WAKE order
            (module docstring's "never both set").
        requested_at: When the order was written.
        handled_at: When `run_cluster_tick`/`run_release_tick` acted on it; None while pending.
    """

    id: str
    kind: OrderKind
    provider: str | None
    requested_at: datetime
    handled_at: datetime | None = None
    lease_id: str | None = None


def new_order_id(clock: Clock) -> str:
    """Mint a fresh order id (module docstring: `IdKind.EVENT`'s own ULID minting, reused)."""
    return new_id(IdKind.EVENT, clock)


class OrderStore(Protocol):
    """Persist ClusterOrder rows; implementations must be safe to call concurrently."""

    async def put_order(self, order: ClusterOrder) -> None:
        """Insert `order`, keyed by `order.id`.

        Args:
            order: The order to store; a fresh row (no earlier order shares its id).
        """
        ...

    async def pending(self) -> tuple[ClusterOrder, ...]:
        """Return every order with no `handled_at` yet, oldest `requested_at` first."""
        ...

    async def mark_handled(self, order_id: str, handled_at: datetime) -> None:
        """Set `order_id`'s own `handled_at`, so it leaves `pending()`'s own result.

        Idempotent: an unknown or already-handled id is a no-op (module docstring).

        Args:
            order_id: The order to mark handled.
            handled_at: When it was handled.
        """
        ...


class InMemoryOrderStore:
    """The non-durable OrderStore: one dict, for tests and a Queen with no database file yet."""

    def __init__(self) -> None:
        """Build an empty store: no orders yet."""
        self._orders: dict[str, ClusterOrder] = {}

    async def put_order(self, order: ClusterOrder) -> None:
        """Insert `order`, keyed by `order.id`; see `OrderStore.put_order`."""
        self._orders[order.id] = order

    async def pending(self) -> tuple[ClusterOrder, ...]:
        """Return every unhandled order, oldest first; see `OrderStore.pending`."""
        unhandled = (order for order in self._orders.values() if order.handled_at is None)
        return tuple(sorted(unhandled, key=lambda order: (order.requested_at, order.id)))

    async def mark_handled(self, order_id: str, handled_at: datetime) -> None:
        """Set `order_id`'s own `handled_at`; see `OrderStore.mark_handled`."""
        order = self._orders.get(order_id)
        if order is None:
            return  # Unknown id: idempotent no-op (module docstring).
        self._orders[order_id] = _with_handled_at(order, handled_at)


def apply_order_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.queen.cluster`.

    Synchronous, like every function `hivemind.common.migrations` exports; `SqliteOrderStore.
    create` is the one caller, and it runs this under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


_INSERT_ORDER_SQL = (
    "INSERT INTO cluster_orders (id, kind, provider, requested_at, handled_at, lease_id) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_SELECT_PENDING_SQL = (
    "SELECT id, kind, provider, requested_at, handled_at, lease_id FROM cluster_orders "
    "WHERE handled_at IS NULL ORDER BY requested_at, id"
)
_UPDATE_HANDLED_SQL = "UPDATE cluster_orders SET handled_at = ? WHERE id = ?"


class SqliteOrderStore:
    """The durable OrderStore: one `cluster_orders` table, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `cluster_orders` (normally
                produced by `create`, which applies the migration first).
        """
        self._connection = connection
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled
        # await can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-orders")
        self._lock = asyncio.Lock()  # Serialises every method, matching SqliteLedgerStore's own.

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteOrderStore:
        """Apply this subsystem's migration and wrap `connection`.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            clock: Injected clock, used for migration timestamps.

        Returns:
            A SqliteOrderStore whose `cluster_orders` table exists and is current.
        """
        await asyncio.to_thread(apply_order_migrations, connection, clock)
        return cls(connection)

    async def put_order(self, order: ClusterOrder) -> None:
        """Insert `order`, keyed by `order.id`; see `OrderStore.put_order`."""
        async with self._lock:
            await self._thread.run(_insert_order, self._connection, order)

    async def pending(self) -> tuple[ClusterOrder, ...]:
        """Return every unhandled order, oldest first; see `OrderStore.pending`."""
        async with self._lock:
            rows = await self._thread.run(
                lambda: self._connection.execute(_SELECT_PENDING_SQL).fetchall()
            )
        return tuple(_order_from_row(row) for row in rows)

    async def mark_handled(self, order_id: str, handled_at: datetime) -> None:
        """Set `order_id`'s own `handled_at`; see `OrderStore.mark_handled`."""
        async with self._lock:
            await self._thread.run(
                lambda: self._connection.execute(
                    _UPDATE_HANDLED_SQL, (handled_at.isoformat(), order_id)
                )
            )


def _insert_order(connection: sqlite3.Connection, order: ClusterOrder) -> None:
    """Run `_INSERT_ORDER_SQL` for `order`, inside its own implicit transaction."""
    handled = order.handled_at.isoformat() if order.handled_at is not None else None
    connection.execute(
        _INSERT_ORDER_SQL,
        (
            order.id,
            order.kind.value,
            order.provider,
            order.requested_at.isoformat(),
            handled,
            order.lease_id,
        ),
    )


def _order_from_row(row: Sequence[object]) -> ClusterOrder:
    """Build a ClusterOrder from one `cluster_orders` row, column order matching the SELECT."""
    order_id, kind, provider, requested_at, handled_at, lease_id = row
    return ClusterOrder(
        id=str(order_id),
        kind=OrderKind(kind),
        provider=str(provider) if provider is not None else None,
        requested_at=datetime.fromisoformat(str(requested_at)),
        handled_at=datetime.fromisoformat(str(handled_at)) if handled_at is not None else None,
        lease_id=str(lease_id) if lease_id is not None else None,
    )


def _with_handled_at(order: ClusterOrder, handled_at: datetime) -> ClusterOrder:
    """Return a copy of `order` with `handled_at` set (ClusterOrder is frozen)."""
    return ClusterOrder(
        id=order.id,
        kind=order.kind,
        provider=order.provider,
        requested_at=order.requested_at,
        handled_at=handled_at,
        lease_id=order.lease_id,
    )
