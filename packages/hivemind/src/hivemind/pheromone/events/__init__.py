"""Provide PheromoneEvent and its twelve event families: the Pheromone Trail's data model.

Every state-changing action anywhere in the Hive is recorded as one of the event subclasses this
package exports (codingrules section 12). `base` defines the shared shape (`PheromoneEvent`) and
`LlmUsage`, the normalised usage value `llm.call` carries; `families` defines one subclass per
event family plus the JSON codec (`parse_event`, `parse_event_json`) that decodes a stored or
wire-carried event without the caller knowing its family in advance. This face re-exports both
modules' public names so a caller writes `from hivemind.pheromone.events import parse_event`
without knowing the split (codingrules 5.2).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Used by every layer above it, each time it
    mutates state, and by hivemind.pheromone.trail (phase 2.2), the store this data model is
    written to and read from.

Key invariants:
    - Whatever is not re-exported here (the private validators and helpers in base.py and
      families.py) is private to this package (codingrules 5.4).

See Also:
    - .claude/codingrules.md section 12 for the Pheromone Trail rules this package's model follows.
    - hivemind.pheromone.events.base for PheromoneEvent, LlmUsage and the shared validators.
    - hivemind.pheromone.events.families for the family subclasses, the vocabulary, and the codec.
    - hivemind.pheromone.errors for PheromoneError and UnknownEventFamilyError.

Public API:
    - PheromoneEvent: the base every event family subclasses.
    - LlmUsage: the provider-neutral token-and-cost shape an `llm.call` event carries.
    - CellEvent, TaskEvent, AlarmEvent, ForageEvent, MemoryEvent, QueenEvent, WardenEvent,
      ToolEvent, SwarmEvent, CappingEvent, LlmEvent, WorkerEvent: the twelve event families.
    - EVENT_FAMILIES: the family-prefix to class mapping the codec dispatches through.
    - event_class_for, parse_event, parse_event_json: look up a family class, or decode a stored
      event, by its `kind` string.
    - KIND_PATTERN, MAX_ACTOR_CHARS, MAX_PAYLOAD_STRING_CHARS, MAX_PAYLOAD_BYTES, ACTOR_LITERALS,
      FORBIDDEN_PAYLOAD_KEYS, MAX_PROVIDER_CHARS: the bounds and vocab fragments the validators
      above enforce.
"""

from hivemind.pheromone.events.base import (
    ACTOR_LITERALS,
    FORBIDDEN_PAYLOAD_KEYS,
    KIND_PATTERN,
    MAX_ACTOR_CHARS,
    MAX_PAYLOAD_BYTES,
    MAX_PAYLOAD_STRING_CHARS,
    LlmUsage,
    PheromoneEvent,
)
from hivemind.pheromone.events.families import (
    EVENT_FAMILIES,
    MAX_PROVIDER_CHARS,
    AlarmEvent,
    CappingEvent,
    CellEvent,
    ForageEvent,
    LlmEvent,
    MemoryEvent,
    QueenEvent,
    SwarmEvent,
    TaskEvent,
    ToolEvent,
    WardenEvent,
    WorkerEvent,
    event_class_for,
    parse_event,
    parse_event_json,
)

__all__ = [
    "ACTOR_LITERALS",
    "EVENT_FAMILIES",
    "FORBIDDEN_PAYLOAD_KEYS",
    "KIND_PATTERN",
    "MAX_ACTOR_CHARS",
    "MAX_PAYLOAD_BYTES",
    "MAX_PAYLOAD_STRING_CHARS",
    "MAX_PROVIDER_CHARS",
    "AlarmEvent",
    "CappingEvent",
    "CellEvent",
    "ForageEvent",
    "LlmEvent",
    "LlmUsage",
    "MemoryEvent",
    "PheromoneEvent",
    "QueenEvent",
    "SwarmEvent",
    "TaskEvent",
    "ToolEvent",
    "WardenEvent",
    "WorkerEvent",
    "event_class_for",
    "parse_event",
    "parse_event_json",
]
