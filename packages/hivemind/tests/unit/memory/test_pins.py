"""Tests for hivemind.memory.pins: Pin's shape and add_pin's write-plus-event behaviour.

Fits into the Hive:
    Mirrors src/hivemind/memory/pins.py (codingrules section 3: tests/unit mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.pins for the module under test.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.memory import make_pin
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.pins import Pin, PinSource, add_pin
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


@dataclass(frozen=True, slots=True)
class _ContextAndTrail:
    """A MemoryContext plus the trail its store records events on, for assertions."""

    ctx: MemoryContext
    trail: PheromoneTrail


def _make_context(clock: FakeClock) -> _ContextAndTrail:
    trail = MemoryPheromoneTrail(clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    ctx = MemoryContext(store=InMemoryMemoryStore(trail), identity=identity, clock=clock)
    return _ContextAndTrail(ctx=ctx, trail=trail)


def test_pin_round_trips_through_json() -> None:
    pin = make_pin()

    restored = Pin.model_validate_json(pin.model_dump_json())

    assert restored == pin


def test_pin_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        make_pin(text="")


async def test_add_pin_stores_it_and_records_memory_pinned_on_the_trail() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    pin = make_pin(clock=clock, source=PinSource.MANIFEST)

    await add_pin(pin, context_and_trail.ctx)

    assert await context_and_trail.ctx.store.list_pins(HoneyClearance.C2) == (pin,)
    events = await context_and_trail.trail.query(TrailQuery(subject_id=pin.id))
    assert len(events) == 1
    assert events[0].kind == "memory.pinned"
