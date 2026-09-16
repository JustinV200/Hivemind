"""Tests for hivemind.queen.cluster.orders: ClusterOrder and its two OrderStore implementations.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/orders.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only. `_ContractCases` is shared, not a test class itself
      (pytest would try to collect a `Test*`-named class as tests; this one is deliberately not).

See Also:
    - hivemind.queen.cluster.orders for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.common.sqlite import connect
from hivemind.queen.cluster.orders import (
    ClusterOrder,
    InMemoryOrderStore,
    OrderKind,
    OrderStore,
    SqliteOrderStore,
    new_order_id,
)
from waggle.clock import FakeClock


def _order(clock: FakeClock, *, kind: OrderKind, provider: str | None = None) -> ClusterOrder:
    """Build one fresh ClusterOrder, minted against `clock`."""
    return ClusterOrder(
        id=new_order_id(clock), kind=kind, provider=provider, requested_at=clock.now()
    )


class _ContractCases:
    """Every OrderStore.pending/put_order/mark_handled case, run against both implementations.

    Not itself collected as tests (its name does not start with `Test`, matching codingrules
    14.3's "a contract test suite parametrised over all implementations" without pytest's own
    class-based parametrisation, which this repository does not otherwise use).
    """

    @staticmethod
    async def pending_is_empty_for_a_fresh_store(store: OrderStore) -> None:
        assert await store.pending() == ()

    @staticmethod
    async def put_order_appears_in_pending(store: OrderStore) -> None:
        clock = FakeClock()
        order = _order(clock, kind=OrderKind.CLUSTER, provider="anthropic")

        await store.put_order(order)

        pending = await store.pending()
        assert len(pending) == 1
        assert pending[0].id == order.id
        assert pending[0].kind is OrderKind.CLUSTER
        assert pending[0].provider == "anthropic"
        assert pending[0].handled_at is None

    @staticmethod
    async def cluster_order_with_no_provider_round_trips_as_none(store: OrderStore) -> None:
        clock = FakeClock()
        order = _order(clock, kind=OrderKind.CLUSTER, provider=None)

        await store.put_order(order)

        pending = await store.pending()
        assert pending[0].provider is None

    @staticmethod
    async def a_wake_order_is_consumed_once_and_only_once(store: OrderStore) -> None:
        """Roadmap step 4.9: a WAKE row is consumed once, never re-acted on by a later poll."""
        clock = FakeClock()
        order = _order(clock, kind=OrderKind.WAKE, provider="anthropic")
        await store.put_order(order)

        first_pending = await store.pending()
        assert len(first_pending) == 1
        await store.mark_handled(order.id, clock.now())

        assert await store.pending() == ()

    @staticmethod
    async def mark_handled_on_an_unknown_id_is_a_no_op(store: OrderStore) -> None:
        await store.mark_handled("does-not-exist", FakeClock().now())  # Must not raise.

    @staticmethod
    async def mark_handled_twice_is_idempotent(store: OrderStore) -> None:
        clock = FakeClock()
        order = _order(clock, kind=OrderKind.CLUSTER)
        await store.put_order(order)
        await store.mark_handled(order.id, clock.now())

        await store.mark_handled(order.id, clock.now())  # Must not raise a second time.

        assert await store.pending() == ()

    @staticmethod
    async def pending_orders_the_oldest_request_first(store: OrderStore) -> None:
        clock = FakeClock()
        first = _order(clock, kind=OrderKind.CLUSTER, provider="anthropic")
        clock.advance(1.0)
        second = _order(clock, kind=OrderKind.CLUSTER, provider="local")
        # Insert out of order; pending() must still return oldest requested_at first.
        await store.put_order(second)
        await store.put_order(first)

        pending = await store.pending()

        assert [order.id for order in pending] == [first.id, second.id]


async def test_in_memory_store_contract() -> None:
    store = InMemoryOrderStore()
    await _ContractCases.pending_is_empty_for_a_fresh_store(store)


async def test_in_memory_store_put_order() -> None:
    await _ContractCases.put_order_appears_in_pending(InMemoryOrderStore())


async def test_in_memory_store_none_provider() -> None:
    await _ContractCases.cluster_order_with_no_provider_round_trips_as_none(InMemoryOrderStore())


async def test_in_memory_store_wake_consumed_once() -> None:
    await _ContractCases.a_wake_order_is_consumed_once_and_only_once(InMemoryOrderStore())


async def test_in_memory_store_mark_handled_unknown_id() -> None:
    await _ContractCases.mark_handled_on_an_unknown_id_is_a_no_op(InMemoryOrderStore())


async def test_in_memory_store_mark_handled_idempotent() -> None:
    await _ContractCases.mark_handled_twice_is_idempotent(InMemoryOrderStore())


async def test_in_memory_store_orders_oldest_first() -> None:
    await _ContractCases.pending_orders_the_oldest_request_first(InMemoryOrderStore())


async def _sqlite_store(tmp_path: Path, name: str) -> SqliteOrderStore:
    """Build a fresh SqliteOrderStore over its own file under `tmp_path`."""
    connection = connect(tmp_path / f"{name}.db")
    return await SqliteOrderStore.create(connection, FakeClock())


async def test_sqlite_store_contract(tmp_path: Path) -> None:
    await _ContractCases.pending_is_empty_for_a_fresh_store(await _sqlite_store(tmp_path, "empty"))


async def test_sqlite_store_put_order(tmp_path: Path) -> None:
    await _ContractCases.put_order_appears_in_pending(await _sqlite_store(tmp_path, "put"))


async def test_sqlite_store_none_provider(tmp_path: Path) -> None:
    await _ContractCases.cluster_order_with_no_provider_round_trips_as_none(
        await _sqlite_store(tmp_path, "none_provider")
    )


async def test_sqlite_store_wake_consumed_once(tmp_path: Path) -> None:
    await _ContractCases.a_wake_order_is_consumed_once_and_only_once(
        await _sqlite_store(tmp_path, "wake")
    )


async def test_sqlite_store_mark_handled_unknown_id(tmp_path: Path) -> None:
    await _ContractCases.mark_handled_on_an_unknown_id_is_a_no_op(
        await _sqlite_store(tmp_path, "unknown")
    )


async def test_sqlite_store_mark_handled_idempotent(tmp_path: Path) -> None:
    await _ContractCases.mark_handled_twice_is_idempotent(
        await _sqlite_store(tmp_path, "idempotent")
    )


async def test_sqlite_store_orders_oldest_first(tmp_path: Path) -> None:
    await _ContractCases.pending_orders_the_oldest_request_first(
        await _sqlite_store(tmp_path, "ordering")
    )


async def test_sqlite_store_applies_its_migration_and_is_reusable_across_connections(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hive.db"
    clock = FakeClock()
    store = await SqliteOrderStore.create(connect(db), clock)
    order = _order(clock, kind=OrderKind.CLUSTER, provider="anthropic")
    await store.put_order(order)

    # A fresh connection to the same file sees the same row: durable, not in-memory only.
    second_store = await SqliteOrderStore.create(connect(db), clock)

    pending = await second_store.pending()
    assert len(pending) == 1
    assert pending[0].id == order.id
