"""Tests for hivemind.entrance.push.store.sqlite: the push tables' migrations and durability.

What every SubscriptionStore does is in tests/contracts/test_push_subscription_store_contract.py;
this module covers what only the SQLite store does: its own migration series in the shared schema
table, state that survives a new connection (a restart), and the foreign-key cascade.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/store/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.store.sqlite for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from unit.entrance.push.support import (
    SteppingClock,
    UserAgent,
    web_push_subscription,
    webhook_subscription,
)

from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.entrance.push.store import SUBSYSTEM, SqliteSubscriptionStore, apply_push_migrations

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # A question's id.


async def test_create_records_the_entrance_push_series_once(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = SteppingClock()
    await SqliteSubscriptionStore.create(connect(db), clock)
    connection = connect(db)

    applied_again = apply_push_migrations(connection, clock)

    assert SUBSYSTEM == "entrance_push"
    assert applied_versions(connection, SUBSYSTEM) == (1,)
    assert applied_again == ()


async def test_subscriptions_and_deliveries_survive_a_new_connection(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = SteppingClock()
    store = await SqliteSubscriptionStore.create(connect(db), clock)
    hook = webhook_subscription(clock)
    push = web_push_subscription(clock, UserAgent())
    await store.add(hook)
    await store.add(push)
    await store.record_delivery(_REF, [push.id], clock.now())

    reopened = await SqliteSubscriptionStore.create(connect(db), clock)

    assert set(await reopened.list_all()) == {hook, push}
    assert await reopened.recipients(_REF) == (push,)


async def test_deleting_a_subscription_cascades_to_its_delivery_rows(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = SteppingClock()
    store = await SqliteSubscriptionStore.create(connect(db), clock)
    hook = webhook_subscription(clock)
    await store.add(hook)
    await store.record_delivery(_REF, [hook.id], clock.now())

    await store.delete(hook.id)

    rows = connect(db).execute("SELECT COUNT(*) FROM entrance_push_deliveries").fetchone()
    assert rows[0] == 0
