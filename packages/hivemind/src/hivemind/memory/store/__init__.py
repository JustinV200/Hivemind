"""Persist pins, notes, Handoffs, episodes and Bee Bread entries: the durable half of memory v0/4.2.

`protocol` fixes the one seam both implementations honour: every write commits its row and its
`MemoryEvent` together (codingrules section 12). `memory` is an in-process fake for tests and
demos; `sqlite` is the durable store, backed by five tables in the Hive's single SQLite file
(ADR-0006). `migrations` holds the numbered `.sql` series `sqlite.py` applies.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by hivemind.memory.pins, .notes,
    .episodes, .checkpoint, .demote and .bee_bread through `hivemind.memory.context.MemoryContext.
    store`. Calls into hivemind.cell, hivemind.common, hivemind.memory (bee_bread, episodes,
    errors, handoff, notes, pins) and hivemind.pheromone.

Key invariants:
    - Whatever is not re-exported here is private to this package (codingrules 5.4).
    - hivemind.memory.store.sqlite's only DELETE statements are remove_pin, remove_note,
      purge_episodes_before, and the per-author note eviction inside add_note; no other write
      path ever rewrites or removes a row.

See Also:
    - .claude/codingrules.md section 12 for the same-transaction rule every implementation follows.
    - hivemind.memory.store.protocol, .memory and .sqlite for the modules behind this face.

Public API:
    - MemoryStore: the protocol every memory store implements (protocol).
    - InMemoryMemoryStore: an in-process MemoryStore for tests and demos (memory).
    - SqliteMemoryStore, apply_memory_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      MemoryStore (sqlite).
"""

from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.memory.store.protocol import MemoryStore
from hivemind.memory.store.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteMemoryStore,
    apply_memory_migrations,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "InMemoryMemoryStore",
    "MemoryStore",
    "SqliteMemoryStore",
    "apply_memory_migrations",
]
