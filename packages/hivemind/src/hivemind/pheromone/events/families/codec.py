"""Define EVENT_FAMILIES and the JSON codec that decodes any Pheromone event by its kind.

Every Pheromone event family (`hivemind.pheromone.events.families.work`, `.resources`,
`.supervisors`) is registered here, once, in the order the vocabulary documents them: the tuple
below is the single place a fourteenth family would be added. `EVENT_FAMILIES` maps each family's
prefix (the part of a kind before the dot) to its class, and the codec dispatches on it:
`event_class_for` finds the class for a kind, `parse_event` validates a JSON-shaped mapping into
it, and `parse_event_json` does the same from raw JSON text, so a caller never needs to know which
family a stored or wire-carried event belongs to before decoding it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.events.
    families`. Read by `hivemind.pheromone.trail` (the segment codec) and every reader of a stored
    event. Calls into `hivemind.pheromone.events.base`, `hivemind.pheromone.errors` and the three
    family modules beside this one only.

Key invariants:
    - Every class's FAMILY is unique across EVENT_FAMILIES (asserted when this module is
      imported, by _build_event_families); two families never share a prefix.
    - parse_event and parse_event_json are the only JSON entry points: both dispatch on `kind`
      alone (codingrules section 9: "Pydantic models are the only thing that reads JSON").

See Also:
    - .claude/codingrules.md section 12 for the audit-log rules the vocabulary follows.
    - hivemind.pheromone.events.base for PheromoneEvent, LlmUsage and the shared validators.
    - hivemind.pheromone.errors for UnknownEventFamilyError, raised by event_class_for.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from hivemind.pheromone.errors import UnknownEventFamilyError
from hivemind.pheromone.events.base import PheromoneEvent
from hivemind.pheromone.events.families.resources import (
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

__all__ = ["EVENT_FAMILIES", "event_class_for", "parse_event", "parse_event_json"]

# Every family class, in the order the vocabulary is documented; the tuple, not the mapping built
# from it, is the single place a fourteenth family would be added.
_FAMILY_CLASSES: tuple[type[PheromoneEvent], ...] = (
    CellEvent,
    TaskEvent,
    AlarmEvent,
    ForageEvent,
    MemoryEvent,
    QueenEvent,
    WardenEvent,
    ToolEvent,
    SwarmEvent,
    CappingEvent,
    LlmEvent,
    WorkerEvent,
    GuardEvent,
)


def _build_event_families(
    classes: tuple[type[PheromoneEvent], ...],
) -> Mapping[str, type[PheromoneEvent]]:
    """Map each class's FAMILY to itself, asserting no two classes share a FAMILY.

    Args:
        classes: Every family subclass to register, in declaration order.

    Returns:
        An immutable family-prefix to class mapping with one entry per element of `classes`.
    """
    families: dict[str, type[PheromoneEvent]] = {}
    for event_cls in classes:
        # A collision here is a programming error in this package, not bad input, so it fails at
        # import time (module-level, pure computation -- codingrules 5.5 allows this) rather than
        # waiting to be discovered by whichever caller happens to hit the clash first.
        if event_cls.FAMILY in families:
            existing = families[event_cls.FAMILY]
            raise AssertionError(
                f"family {event_cls.FAMILY!r} is registered by both {existing.__name__} and "
                f"{event_cls.__name__}."
            )
        families[event_cls.FAMILY] = event_cls
    return families


EVENT_FAMILIES: Mapping[str, type[PheromoneEvent]] = _build_event_families(_FAMILY_CLASSES)


def event_class_for(kind: str) -> type[PheromoneEvent]:
    """Look up the PheromoneEvent subclass registered for `kind`'s family segment.

    Args:
        kind: A candidate kind string, e.g. `"task.submitted"`.

    Returns:
        The subclass whose `FAMILY` equals the segment of `kind` before its first `.`.

    Raises:
        UnknownEventFamilyError: `kind` has no `.`, or its family segment is not in
            `EVENT_FAMILIES`.
    """
    family, sep, _ = kind.partition(".")
    if not sep:
        raise UnknownEventFamilyError(f"kind {kind!r} is not '<family>.<name>' shaped.")
    event_cls = EVENT_FAMILIES.get(family)
    if event_cls is None:
        raise UnknownEventFamilyError(f"kind {kind!r} names an unknown event family {family!r}.")
    return event_cls


def parse_event(data: Mapping[str, object]) -> PheromoneEvent:
    """Decode `data` into the right PheromoneEvent subclass, chosen by its `kind` field.

    The one JSON-codec entry point for a Pheromone event: a caller never needs to know which
    family a stored or wire-carried event belongs to before decoding it.

    Args:
        data: A JSON-object-shaped mapping, as produced by `PheromoneEvent.model_dump(mode=
            "json")` or read back from storage.

    Returns:
        A validated instance of the subclass `data["kind"]` selects.

    Raises:
        UnknownEventFamilyError: `data` has no string `kind` field, or `event_class_for` rejects
            it.
        pydantic.ValidationError: `data` fails the selected subclass's own field validation.
    """
    kind = data.get("kind")
    if not isinstance(kind, str):
        raise UnknownEventFamilyError(f"event data has no string 'kind' field: {kind!r}.")
    event_cls = event_class_for(kind)
    return event_cls.model_validate(data)


def parse_event_json(raw: str | bytes) -> PheromoneEvent:
    """Parse `raw` as JSON and decode it into the right PheromoneEvent subclass.

    Args:
        raw: A JSON document, as text or UTF-8 bytes, expected to be a single JSON object.

    Returns:
        A validated instance of the subclass the document's `kind` field selects.

    Raises:
        UnknownEventFamilyError: `raw` does not decode to a JSON object, or `parse_event` rejects
            the decoded mapping.
        json.JSONDecodeError: `raw` is not valid JSON at all.
        pydantic.ValidationError: The decoded object fails the selected subclass's own validation.
    """
    document = json.loads(raw)
    if not isinstance(document, Mapping):
        raise UnknownEventFamilyError(f"event JSON is not an object: {type(document).__name__}.")
    return parse_event(document)
