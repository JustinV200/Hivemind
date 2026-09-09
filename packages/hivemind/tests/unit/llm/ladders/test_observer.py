"""Tests for hivemind.llm.ladders.observer: LadderObserver, FallbackNote and its implementations.

Fits into the Hive:
    Mirrors src/hivemind/llm/ladders/observer.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.ladders.observer for the module under test.
"""

from __future__ import annotations

from hivemind.forage.slots import ModelSlot
from hivemind.llm.ladders.observer import (
    FALLBACK_KIND,
    FallbackNote,
    FallbackReason,
    NullLadderObserver,
    TrailLadderObserver,
)
from hivemind.llm.ladders.structured import Rung
from hivemind.pheromone import LlmEvent, MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


async def test_null_ladder_observer_discards_the_note_without_raising() -> None:
    observer = NullLadderObserver()
    note = FallbackNote(
        slot=ModelSlot.WORKER,
        from_binding="worker",
        to_binding=None,
        from_rung=Rung.NATIVE,
        to_rung=Rung.JSON_MODE,
        reason=FallbackReason.RUNG_EXHAUSTED,
    )

    await observer.on_fallback(note)  # Does not raise; there is nothing else to assert.


def _build_trail_observer() -> tuple[TrailLadderObserver, MemoryPheromoneTrail]:
    """Wire a TrailLadderObserver over a fresh MemoryPheromoneTrail, sharing one clock."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    observer = TrailLadderObserver(trail, new_hive_id(clock), new_node_id(clock), "human", clock)
    return observer, trail


async def test_trail_ladder_observer_records_an_llm_fallback_event() -> None:
    observer, trail = _build_trail_observer()
    note = FallbackNote(
        slot=ModelSlot.WORKER,
        from_binding="worker",
        to_binding=None,
        from_rung=Rung.NATIVE,
        to_rung=Rung.JSON_MODE,
        reason=FallbackReason.RUNG_EXHAUSTED,
    )

    await observer.on_fallback(note)

    events = await trail.query(TrailQuery())
    assert len(events) == 1
    assert events[0].kind == FALLBACK_KIND


async def test_trail_ladder_observer_rung_step_down_payload_has_no_binding_change() -> None:
    observer, trail = _build_trail_observer()
    note = FallbackNote(
        slot=ModelSlot.WORKER,
        from_binding="worker",
        to_binding=None,
        from_rung=Rung.JSON_MODE,
        to_rung=Rung.PROMPTED,
        reason=FallbackReason.RUNG_EXHAUSTED,
    )

    await observer.on_fallback(note)

    (event,) = await trail.query(TrailQuery())
    assert isinstance(event, LlmEvent)
    assert event.payload["from_binding"] == "worker"
    assert event.payload["to_binding"] is None
    assert event.payload["from_rung"] == "JSON_MODE"
    assert event.payload["to_rung"] == "PROMPTED"
    assert event.payload["reason"] == "RUNG_EXHAUSTED"
    assert event.slot == "WORKER"


async def test_trail_ladder_observer_binding_fallback_payload_has_no_rung_change() -> None:
    observer, trail = _build_trail_observer()
    note = FallbackNote(
        slot=ModelSlot.WORKER,
        from_binding="worker",
        to_binding="local_worker",
        from_rung=None,
        to_rung=None,
        reason=FallbackReason.PROVIDER_UNAVAILABLE,
    )

    await observer.on_fallback(note)

    (event,) = await trail.query(TrailQuery())
    assert event.payload["to_binding"] == "local_worker"
    assert event.payload["from_rung"] is None
    assert event.payload["to_rung"] is None
    assert event.payload["reason"] == "PROVIDER_UNAVAILABLE"


async def test_trail_ladder_observer_mints_a_subject_id_distinct_from_the_events_own_id() -> None:
    observer, trail = _build_trail_observer()
    note = FallbackNote(
        slot=ModelSlot.WORKER,
        from_binding="worker",
        to_binding="local_worker",
        from_rung=None,
        to_rung=None,
        reason=FallbackReason.RATE_LIMITED,
    )

    await observer.on_fallback(note)

    (event,) = await trail.query(TrailQuery())
    assert event.subject_id != event.id
    assert event.subject_id.startswith("event_")


async def test_trail_ladder_observer_records_one_event_per_call() -> None:
    observer, trail = _build_trail_observer()
    note = FallbackNote(
        slot=ModelSlot.WORKER,
        from_binding="worker",
        to_binding="local_worker",
        from_rung=None,
        to_rung=None,
        reason=FallbackReason.PROVIDER_UNAVAILABLE,
    )

    await observer.on_fallback(note)
    await observer.on_fallback(note)

    events = await trail.query(TrailQuery())
    assert len(events) == 2
    assert events[0].id != events[1].id
