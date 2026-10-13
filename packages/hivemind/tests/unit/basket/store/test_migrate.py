"""Tests for hivemind.basket.store.migrate: the numbered series applies once and creates the tables.

Fits into the Hive:
    Mirrors src/hivemind/basket/store/migrate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.store.migrate for the module under test.
"""

from __future__ import annotations

from hivemind.basket import apply_basket_migrations
from hivemind.common.sqlite import connect
from waggle.clock import FakeClock

_TABLES = {"basket_blobs", "basket_residency", "basket_shipments"}


def test_the_series_creates_the_three_basket_tables() -> None:
    connection = connect(":memory:")

    applied = apply_basket_migrations(connection, FakeClock())

    names = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert applied == (1,) and names >= _TABLES


def test_applying_twice_applies_nothing_the_second_time() -> None:
    connection = connect(":memory:")
    clock = FakeClock()
    apply_basket_migrations(connection, clock)

    assert apply_basket_migrations(connection, clock) == ()
