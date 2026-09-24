"""Provide the thirteen Pheromone event families and the JSON codec built on them.

Every state-changing action in the Hive writes a `PheromoneEvent` (`hivemind.pheromone.events.
base`) whose `kind` is one of the strings these modules document. Together they are the normative
vocabulary: the complete, stable set of `kind` strings the Observation Hive and a human auditor
read, and the only place a new one may be added (codingrules section 12, roadmap 2.1). Each family
is a `PheromoneEvent` subclass that fixes `FAMILY` (the prefix before the dot) and `KINDS` (every
kind string that family accepts); the base class's validators refuse anything outside a
subclass's own `KINDS` or `FAMILY`. The package split the vocabulary by what it records, once the
single module reached its size limit (codingrules 5.2): `work` (cell, task, alarm, worker),
`resources` (forage, memory, tool, swarm, llm), `supervisors` (queen, warden, capping, guard), and
`codec`, the registry and JSON codec that decode any of them by kind. Each module's docstring
holds its own families' vocabulary: family -> kind -> when it is recorded.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Every layer above Layer 1 constructs one
    of these subclasses each time it mutates state; `hivemind.pheromone.trail` is the first
    consumer of parse_event/parse_event_json, its JSON segment codec.

Key invariants:
    - Every class's FAMILY is unique across EVENT_FAMILIES (asserted at import by the codec); two
      families never share a prefix.
    - Every class's KINDS contains only strings whose family segment equals that class's own
      FAMILY (`PheromoneEvent._validate_kind` enforces this per instance).

See Also:
    - .claude/codingrules.md section 12 for the audit-log rules this vocabulary follows.
    - hivemind.pheromone.events.base for PheromoneEvent, LlmUsage and the shared validators.
    - hivemind.guard.policy.catalogue for where each kind's action is authorised (ADR-0031).

Public API:
    - CellEvent, TaskEvent, AlarmEvent, WorkerEvent: the work and where it runs (work).
    - ForageEvent, MemoryEvent, ToolEvent, SwarmEvent, LlmEvent, MAX_PROVIDER_CHARS: what the
      work draws on (resources).
    - QueenEvent, WardenEvent, CappingEvent, GuardEvent: the supervisors and their gates
      (supervisors).
    - EVENT_FAMILIES, event_class_for, parse_event, parse_event_json: the registry and the JSON
      codec (codec).
"""

from hivemind.pheromone.events.families.codec import (
    EVENT_FAMILIES,
    event_class_for,
    parse_event,
    parse_event_json,
)
from hivemind.pheromone.events.families.resources import (
    MAX_PROVIDER_CHARS,
    ForageEvent,
    LlmEvent,
    MemoryEvent,
    SwarmEvent,
    ToolEvent,
)
from hivemind.pheromone.events.families.supervisors import (
    CappingEvent,
    GuardEvent,
    QueenEvent,
    WardenEvent,
)
from hivemind.pheromone.events.families.work import AlarmEvent, CellEvent, TaskEvent, WorkerEvent

__all__ = [
    "EVENT_FAMILIES",
    "MAX_PROVIDER_CHARS",
    "AlarmEvent",
    "CappingEvent",
    "CellEvent",
    "ForageEvent",
    "GuardEvent",
    "LlmEvent",
    "MemoryEvent",
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
