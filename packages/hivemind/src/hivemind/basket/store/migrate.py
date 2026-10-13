"""Apply the Basket's own numbered migration series to an open connection.

Mirrors `hivemind.honey_store.schema.migrate`: one subsystem name (`"basket"`), one dotted package
path to the numbered `.sql` files and one call into the shared runner in
`hivemind.common.migrations`. The Basket does not write Pheromone Trail events from inside its
store (its door returns them to the caller), so unlike the Honey Store it needs no
`pheromone_events` table on the connection first.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store`. Called
    by `SqliteBasketStore.create` under `asyncio.to_thread`, and by a composition root that
    prepares a database before opening the store.

Key invariants:
    - Synchronous, like every function `hivemind.common.migrations` exports: callers run it off
      the event loop (codingrules section 11).
    - `importlib.resources` is used here only to read the `.sql` files as data; it never loads
      code (the inert-blob test allows exactly this one use).

See Also:
    - hivemind.common.migrations for load_migrations/apply_migrations, this module's callees.
    - hivemind.basket.store.migrations for the numbered `.sql` files.
"""

from __future__ import annotations

import importlib.resources
import sqlite3

from hivemind.common.migrations import apply_migrations, load_migrations
from waggle.clock import Clock

SUBSYSTEM = "basket"  # Keys this subsystem's rows in the shared schema_migrations table.
# Dotted package path importlib.resources.files() reads the numbered .sql files from.
MIGRATIONS_PACKAGE = "hivemind.basket.store.migrations"

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "apply_basket_migrations"]


def apply_basket_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.basket.store.migrations`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending; empty when current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)
