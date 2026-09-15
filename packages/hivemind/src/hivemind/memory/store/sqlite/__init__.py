"""The durable MemoryStore: five SQLite tables in the Hive's single database file (ADR-0006).

Split into a package (codingrules 5.2) once the fifth table (`memory_bee_bread`, roadmap step 4.2)
pushed the combined SQL past codingrules 5.1's file-size limit: `records` holds the original four
tables' SQL and transactions (pins, notes, handoffs, episodes); `bee_bread` holds the fifth's;
`store` holds `SqliteMemoryStore` itself, `create` and `apply_memory_migrations`, delegating to
both siblings.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by
    `hivemind.memory.store.__init__` and the contract suite. Calls into hivemind.cell,
    hivemind.common, hivemind.memory (bee_bread, episodes, errors, handoff, notes, pins) and
    hivemind.pheromone.

Key invariants:
    - Whatever is not re-exported here is private to this package (codingrules 5.4): `records` and
      `bee_bread` are internal collaborators of `store`, never imported from outside this package.

See Also:
    - hivemind.memory.store.sqlite.store for SqliteMemoryStore, the class this face re-exports.
    - hivemind.memory.store.sqlite.records and .bee_bread for the SQL behind it.

Public API:
    - SqliteMemoryStore, apply_memory_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      MemoryStore (store).
"""

from hivemind.memory.store.sqlite.store import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteMemoryStore,
    apply_memory_migrations,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteMemoryStore",
    "apply_memory_migrations",
]
