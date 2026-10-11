"""Build one Pheromone Trail event of any kind, for tests that grade or count a trail.

`make_trail_event("llm.call", slot="WORKER", provider="fake", usage=...)` returns a validated event
of whichever family the kind belongs to (`hivemind.pheromone.events.families.event_class_for`),
with the ids, timestamp and actor every event needs filled in, so a grader's test says only what
the event is. `make_trail` builds a list from `(kind, extras)` pairs with strictly increasing
times, the order a real trail has.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the evaluation harness
    tests.

Key invariants:
    - Every event is built through its family's own model, so an invalid kind or payload fails the
      test at build time exactly as it would on a real trail.
    - Timestamps come from the injected clock and rise by one tick per event in `make_trail`.

See Also:
    - hivemind.pheromone.events.families for the event models these builders construct.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hivemind.pheromone import LlmUsage, PheromoneEvent
from hivemind.pheromone.events.families import event_class_for
from waggle.clock import Clock, FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_task_id

__all__ = ["make_llm_call", "make_trail", "make_trail_event"]

_HIVE_CLOCK = FakeClock()
_HIVE_ID = new_hive_id(_HIVE_CLOCK)
_NODE_ID = new_node_id(_HIVE_CLOCK)


def make_trail_event(kind: str, clock: Clock | None = None, **fields: object) -> PheromoneEvent:
    """Build a valid event of `kind`.

    Args:
        kind: A `<family>.<name>` kind from the audit vocabulary.
        clock: Source of the event's id and time; a fresh FakeClock when omitted.
        **fields: Field values that replace the defaults (`payload`, `slot`, `usage`, ...).

    Returns:
        An event of the family `kind` belongs to.
    """
    active = clock if clock is not None else FakeClock()
    base: dict[str, object] = {
        "id": new_event_id(active),
        "hive_id": _HIVE_ID,
        "node_id": _NODE_ID,
        "at": active.now(),
        "actor": "system",
        "kind": kind,
        "subject_id": new_task_id(active),
    }
    base.update(fields)
    return event_class_for(kind)(**base)


def make_llm_call(
    clock: Clock | None = None,
    slot: str = "WORKER",
    cost_usd: float = 0.0,
    priced: bool = True,
) -> PheromoneEvent:
    """Build an `llm.call` event for `slot` carrying a small priced usage."""
    usage = LlmUsage(
        input_tokens=10, output_tokens=5, cached_tokens=0, cost_usd=cost_usd, priced=priced
    )
    return make_trail_event("llm.call", clock, slot=slot, provider="fake", usage=usage)


def make_trail(
    steps: Sequence[tuple[str, Mapping[str, object]] | str], clock: FakeClock | None = None
) -> tuple[PheromoneEvent, ...]:
    """Build a trail from kinds (or `(kind, fields)` pairs), each a tick after the last.

    Args:
        steps: The events to build, in order.
        clock: Shared clock; a fresh FakeClock when omitted. It advances one second per event.

    Returns:
        The events, oldest first.
    """
    active = clock if clock is not None else FakeClock()
    events: list[PheromoneEvent] = []
    for step in steps:
        kind, fields = (step, {}) if isinstance(step, str) else step
        events.append(make_trail_event(kind, active, **fields))
        active.advance(1.0)
    return tuple(events)
