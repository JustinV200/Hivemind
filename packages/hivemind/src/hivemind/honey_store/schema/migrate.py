"""Apply the Honey Store's own numbered migration series to an open connection.

Mirrors `hivemind.memory.store.sqlite.apply_memory_migrations` and
`hivemind.pheromone.trail.sqlite.apply_pheromone_migrations`: one subsystem name
(`"honey_store"`), one dotted package path to the numbered `.sql` files under
`hivemind.honey_store.schema.migrations`, and one call into the shared runner in
`hivemind.common.migrations`. Kept in its own module, `schema/migrate.py`, rather than folded into
`hivemind.honey_store.store.sqlite.store` (where `apply_memory_migrations` lives for memory),
because roadmap 7.2's brief keeps the on-disk shape (`schema/`) reviewable on its own, separate
from the store that reads and writes it (`store/`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by `hivemind.honey_store.store.sqlite.SqliteHoneyStore.create`, always from inside
    `asyncio.to_thread` since this function is synchronous.

Key invariants:
    - Synchronous, like every function `hivemind.common.migrations` exports: the one caller runs
      it under `asyncio.to_thread` (codingrules section 11).

See Also:
    - hivemind.common.migrations for load_migrations/apply_migrations, this module's own callee.
    - hivemind.honey_store.schema.migrations for the numbered `.sql` files this module applies.
    - hivemind.honey_store.store.sqlite for SqliteHoneyStore.create, the one caller.
"""

from __future__ import annotations

import importlib.resources
import sqlite3

from hivemind.common.migrations import apply_migrations, load_migrations
from waggle.clock import Clock

SUBSYSTEM = "honey_store"  # Keys this subsystem's rows in the shared schema_migrations table.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; a string,
# not a direct package import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.honey_store.schema.migrations"

__all__ = ["MIGRATIONS_PACKAGE", "SUBSYSTEM", "apply_honey_store_migrations"]


def apply_honey_store_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.honey_store.schema.migrations`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`, already carrying
            the `pheromone_events` table (`SqliteHoneyStore.create` checks this first).
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending; empty when the schema
        was already current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)
