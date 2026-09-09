"""Define PheromoneError, the Pheromone Trail subsystem's own error tree.

The Pheromone Trail (`hivemind.pheromone`) is the Hive's append-only audit log: every
state-changing action anywhere in the Hive writes a `PheromoneEvent` here before the action is
considered complete (codingrules section 12). This module holds the two ways that recording or
decoding an event can go wrong on purpose: a store refusing to record an event whose id it has
already seen, and the JSON codec (`hivemind.pheromone.events.parse_event`) meeting a `kind`
string whose family segment names no known event family. Both are `PheromoneError`, the
subsystem's own root, so a caller several layers up can catch one name and know it caught
anything the trail itself raised on purpose (codingrules section 10).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Raised by `hivemind.pheromone.events` (the
    JSON codec) and by the trail store implementations phase 2.2 adds
    (`hivemind.pheromone.trail.sqlite`, `hivemind.pheromone.trail.memory`). Imported by every
    layer above that records or reads trail events.

Key invariants:
    - Every PheromoneError subclass sets its own `code`; none shares a code with another.
    - PheromoneError never inherits from one of `hivemind.common.errors`' six base categories,
      because neither a duplicate event id nor an unknown event family maps cleanly onto any of
      them; both are subsystem-specific conditions the Pheromone Trail alone can detect.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError, the root every subsystem's tree descends from.
    - hivemind.pheromone.events for parse_event, the one place UnknownEventFamilyError is raised
      in this phase.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import HiveMindError

__all__ = ["DuplicateEventError", "PheromoneError", "UnknownEventFamilyError"]


class PheromoneError(HiveMindError):
    """Root of every error `hivemind.pheromone` raises on purpose.

    Subclass this for a specific failure, as `DuplicateEventError` and `UnknownEventFamilyError`
    do below; code that has nothing more specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.pheromone.error"


class DuplicateEventError(PheromoneError):
    """Raise when a `PheromoneTrail.record` call sees an event id the trail already holds.

    The trail is append-only (codingrules section 12): a duplicate id most often means a caller
    retried a write whose first attempt actually succeeded, so the message should carry the id
    that collided.
    """

    code: ClassVar[str] = "hivemind.pheromone.duplicate_event"


class UnknownEventFamilyError(PheromoneError):
    """Raise when a `kind` string names no registered event family, or is not `<family>.<name>`.

    Raised by `hivemind.pheromone.events.event_class_for` and the `parse_event`/`parse_event_json`
    codec built on it: a malformed `kind`, a `kind` whose family segment is not one of the eleven
    in `EVENT_FAMILIES`, or a JSON document with no string `kind` field at all.
    """

    code: ClassVar[str] = "hivemind.pheromone.unknown_family"
