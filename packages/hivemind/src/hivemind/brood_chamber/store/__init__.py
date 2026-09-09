"""Re-export the TaskStore protocol and its two implementations: the store package.

`hivemind.brood_chamber.store.protocol` defines `TaskStore` (codingrules 8.1: a Protocol at every
seam) and `TaskFilter`, the query shape `TaskStore.list_tasks` takes; `hivemind.brood_chamber.
store.memory.MemoryTaskStore` and `hivemind.brood_chamber.store.sqlite.SqliteTaskStore` are its two
implementations, and `hivemind.brood_chamber.store.migrations` is the SQL migration series the
SQLite implementation applies. This file is the package's face: a caller writes `from
hivemind.brood_chamber.store import TaskStore` without knowing the split, while every name stays
defined in the module that names it (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber`. Used by
    `hivemind.brood_chamber.chamber` (roadmap step 2.8) and `hivemind.brood_chamber.__init__`, the
    subsystem's own face. Calls into nothing outside its own three modules and
    `hivemind.common`/`hivemind.pheromone`, which those modules import directly.

Key invariants:
    - This file holds re-exports and __all__ only; the three modules behind it are where every
      name is actually defined.
    - Sibling modules inside `hivemind.brood_chamber` import `protocol`, `memory` and `sqlite` by
      their full module path (`hivemind.brood_chamber.store.protocol`, and so on), never through
      this face, so this package can never be part of a circular import.

See Also:
    - hivemind.brood_chamber.store.protocol, hivemind.brood_chamber.store.memory and
      hivemind.brood_chamber.store.sqlite for the definitions behind this package's public API.
    - hivemind.brood_chamber for the subsystem face this package's own face feeds.

Public API:
    - TaskFilter, TaskStore, check_task_event, and its bounds (MIN_TASK_FILTER_LIMIT,
      MAX_TASK_FILTER_LIMIT, DEFAULT_TASK_FILTER_LIMIT): the store protocol and its query/guard
      (protocol).
    - MemoryTaskStore: an in-process TaskStore for tests and demos (memory).
    - SqliteTaskStore, apply_brood_chamber_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      TaskStore (sqlite).
"""

from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.store.protocol import (
    DEFAULT_TASK_FILTER_LIMIT,
    MAX_TASK_FILTER_LIMIT,
    MIN_TASK_FILTER_LIMIT,
    TaskFilter,
    TaskStore,
    check_task_event,
)
from hivemind.brood_chamber.store.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteTaskStore,
    apply_brood_chamber_migrations,
)

__all__ = [
    "DEFAULT_TASK_FILTER_LIMIT",
    "MAX_TASK_FILTER_LIMIT",
    "MIGRATIONS_PACKAGE",
    "MIN_TASK_FILTER_LIMIT",
    "SUBSYSTEM",
    "MemoryTaskStore",
    "SqliteTaskStore",
    "TaskFilter",
    "TaskStore",
    "apply_brood_chamber_migrations",
    "check_task_event",
]
