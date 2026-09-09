"""Provide the Pheromone Trail: the Hive's append-only audit log, event model and Night Veil purge.

Every state-changing action anywhere in the Hive leaves a `PheromoneEvent` here before the action
counts as complete (codingrules section 12). `events` defines the eleven event families and the
JSON codec; `trail` groups the `PheromoneTrail` protocol, its two implementations and live-tail
follow behind its own face (codingrules 5.6: at most ten modules per directory); `retention` is
the Night Veil boundary, the package's one deletion path. This face re-exports every module's
public names so a caller writes `from hivemind.pheromone import SqlitePheromoneTrail` without
knowing the split (codingrules 5.2).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by every layer above it, each time
    it mutates state. Calls into hivemind.common and waggle.

Key invariants:
    - Whatever is not re-exported here (private validators and helpers inside each module) is
      private to this package (codingrules 5.4).
    - hivemind.pheromone.trail.sqlite carries no `UPDATE` or `DELETE` token anywhere in its own
      source; hivemind.pheromone.retention is the package's only removal path, and only at Night
      Veil teardown (ADR-0007, codingrules section 12).

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies, and section 12
      for the Pheromone Trail and Night Veil rules the whole package follows.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions this package implements.
    - hivemind.pheromone.events, hivemind.pheromone.trail, hivemind.pheromone.retention for the
      modules and packages behind this package's public API.

Public API:
    - PheromoneEvent, LlmUsage and the eleven event families (CellEvent, TaskEvent, AlarmEvent,
      ForageEvent, MemoryEvent, QueenEvent, WardenEvent, ToolEvent, SwarmEvent, CappingEvent,
      LlmEvent), plus EVENT_FAMILIES, event_class_for, parse_event, parse_event_json and the
      vocabulary/validation bounds: KIND_PATTERN, ACTOR_LITERALS, FORBIDDEN_PAYLOAD_KEYS,
      MAX_ACTOR_CHARS, MAX_PAYLOAD_STRING_CHARS, MAX_PAYLOAD_BYTES, MAX_PROVIDER_CHARS.
    - PheromoneTrail: the protocol every trail store implements. TrailQuery, TrailSegment,
      TRAIL_ORDER_KEY, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT: its query and segment shapes.
    - MemoryPheromoneTrail: an in-process PheromoneTrail for tests and demos.
    - SqlitePheromoneTrail: the durable PheromoneTrail, plus apply_pheromone_migrations and
      insert_event (the primitive another store's own transaction calls), SUBSYSTEM and
      MIGRATIONS_PACKAGE.
    - SegmentPurge, SqliteSegmentPurge, MemorySegmentPurge, SideChannelPurger, PurgeReport,
      TrailRecorder, NightVeilTeardownPurge: the Night Veil boundary.
    - follow, DEFAULT_POLL_INTERVAL_S: live-tail the trail.
    - PheromoneError, DuplicateEventError, UnknownEventFamilyError: the error tree, so a
      caller in another subsystem can catch a duplicate id by name.
"""

from hivemind.pheromone.errors import (
    DuplicateEventError,
    PheromoneError,
    UnknownEventFamilyError,
)
from hivemind.pheromone.events import (
    ACTOR_LITERALS,
    EVENT_FAMILIES,
    FORBIDDEN_PAYLOAD_KEYS,
    KIND_PATTERN,
    MAX_ACTOR_CHARS,
    MAX_PAYLOAD_BYTES,
    MAX_PAYLOAD_STRING_CHARS,
    MAX_PROVIDER_CHARS,
    AlarmEvent,
    CappingEvent,
    CellEvent,
    ForageEvent,
    LlmEvent,
    LlmUsage,
    MemoryEvent,
    PheromoneEvent,
    QueenEvent,
    SwarmEvent,
    TaskEvent,
    ToolEvent,
    WardenEvent,
    event_class_for,
    parse_event,
    parse_event_json,
)
from hivemind.pheromone.retention import (
    MemorySegmentPurge,
    NightVeilTeardownPurge,
    PurgeReport,
    SegmentPurge,
    SideChannelPurger,
    SqliteSegmentPurge,
    TrailRecorder,
)
from hivemind.pheromone.trail import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_QUERY_LIMIT,
    MAX_QUERY_LIMIT,
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    TRAIL_ORDER_KEY,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
    TrailSegment,
    apply_pheromone_migrations,
    follow,
    insert_event,
)

__all__ = [
    "ACTOR_LITERALS",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_QUERY_LIMIT",
    "EVENT_FAMILIES",
    "FORBIDDEN_PAYLOAD_KEYS",
    "KIND_PATTERN",
    "MAX_ACTOR_CHARS",
    "MAX_PAYLOAD_BYTES",
    "MAX_PAYLOAD_STRING_CHARS",
    "MAX_PROVIDER_CHARS",
    "MAX_QUERY_LIMIT",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "TRAIL_ORDER_KEY",
    "AlarmEvent",
    "CappingEvent",
    "CellEvent",
    "DuplicateEventError",
    "ForageEvent",
    "LlmEvent",
    "LlmUsage",
    "MemoryEvent",
    "MemoryPheromoneTrail",
    "MemorySegmentPurge",
    "NightVeilTeardownPurge",
    "PheromoneError",
    "PheromoneEvent",
    "PheromoneTrail",
    "PurgeReport",
    "QueenEvent",
    "SegmentPurge",
    "SideChannelPurger",
    "SqlitePheromoneTrail",
    "SqliteSegmentPurge",
    "SwarmEvent",
    "TaskEvent",
    "ToolEvent",
    "TrailQuery",
    "TrailRecorder",
    "TrailSegment",
    "UnknownEventFamilyError",
    "WardenEvent",
    "apply_pheromone_migrations",
    "event_class_for",
    "follow",
    "insert_event",
    "parse_event",
    "parse_event_json",
]
