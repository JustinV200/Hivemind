"""Re-export the PheromoneTrail protocol, its two implementations and follow: the trail package.

`hivemind.pheromone.trail.protocol` defines `PheromoneTrail` (codingrules 8.1: a Protocol at every
seam), `TrailQuery` and `TrailSegment`; `hivemind.pheromone.trail.memory.MemoryPheromoneTrail` and
`hivemind.pheromone.trail.sqlite.SqlitePheromoneTrail` are its two implementations,
`hivemind.pheromone.trail.migrations` is the SQL migration series the SQLite implementation
applies, and `hivemind.pheromone.trail.tail.follow` reads a trail live. This file is the package's
face: a caller writes `from hivemind.pheromone.trail import PheromoneTrail` without knowing the
split, while every name stays defined in the module that names it (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone`. Used by
    `hivemind.pheromone.retention` and `hivemind.pheromone.__init__`, the subsystem's own face.
    Calls into nothing outside its own four modules and `hivemind.pheromone.events`/
    `hivemind.common`/`waggle`, which those modules import directly.

Key invariants:
    - This file holds re-exports and __all__ only; the four modules behind it are where every
      name is actually defined.
    - Sibling modules inside `hivemind.pheromone` import `protocol`, `memory`, `sqlite` and `tail`
      by their full module path (`hivemind.pheromone.trail.protocol`, and so on), never through
      this face, so this package can never be part of a circular import.

See Also:
    - hivemind.pheromone.trail.protocol, hivemind.pheromone.trail.memory,
      hivemind.pheromone.trail.sqlite and hivemind.pheromone.trail.tail for the definitions behind
      this package's public API.
    - hivemind.pheromone for the subsystem face this package's own face feeds.

Public API:
    - PheromoneTrail, TrailQuery, TrailSegment, TRAIL_ORDER_KEY, DEFAULT_QUERY_LIMIT,
      MAX_QUERY_LIMIT: the trail protocol and its query/segment shapes (protocol).
    - MemoryPheromoneTrail: an in-process PheromoneTrail for tests and demos (memory).
    - SqlitePheromoneTrail, apply_pheromone_migrations, insert_event, SUBSYSTEM,
      MIGRATIONS_PACKAGE: the durable PheromoneTrail (sqlite).
    - follow, DEFAULT_POLL_INTERVAL_S: live-tail the trail (tail).
"""

from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import (
    DEFAULT_QUERY_LIMIT,
    MAX_QUERY_LIMIT,
    TRAIL_ORDER_KEY,
    PheromoneTrail,
    TrailQuery,
    TrailSegment,
)
from hivemind.pheromone.trail.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqlitePheromoneTrail,
    apply_pheromone_migrations,
    insert_event,
)
from hivemind.pheromone.trail.tail import DEFAULT_POLL_INTERVAL_S, follow

__all__ = [
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_QUERY_LIMIT",
    "MAX_QUERY_LIMIT",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "TRAIL_ORDER_KEY",
    "MemoryPheromoneTrail",
    "PheromoneTrail",
    "SqlitePheromoneTrail",
    "TrailQuery",
    "TrailSegment",
    "apply_pheromone_migrations",
    "follow",
    "insert_event",
]
