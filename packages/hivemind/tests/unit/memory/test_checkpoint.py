"""Tests for hivemind.memory.checkpoint: write_checkpoint/read_handoff's write, read and refusal.

Fits into the Hive:
    Mirrors src/hivemind/memory/checkpoint.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.checkpoint for the module under test.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.memory import make_handoff
from builders.tasks import make_task

from hivemind.cell import HoneyClearance
from hivemind.memory.checkpoint import read_handoff, write_checkpoint
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.errors import ClearanceError, HandoffNotFoundError
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.memory.store.protocol import MemoryStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id
from waggle.messages import HandoffRef
from waggle.messages import HoneyClearance as WireHoneyClearance


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


async def test_write_checkpoint_then_read_handoff_round_trips() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    handoff = make_handoff(clearance=HoneyClearance.C1)

    ref = await write_checkpoint(handoff, None, context_and_trail.ctx)
    result = await read_handoff(context_and_trail.ctx.store, ref, HoneyClearance.C1)

    assert result == handoff


async def test_write_checkpoint_records_a_memory_checkpoint_event_on_the_trail() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    handoff = make_handoff()

    ref = await write_checkpoint(handoff, None, context_and_trail.ctx)

    events = await context_and_trail.trail.query(TrailQuery(subject_id=ref.event_id))
    assert len(events) == 1
    assert events[0].kind == "memory.checkpoint"
    assert events[0].payload["decision_count"] == len(handoff.decisions)


async def test_write_checkpoint_with_a_task_id_uses_it_as_the_events_subject() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    handoff = make_handoff()
    task = make_task(clock=clock)

    await write_checkpoint(handoff, task.id, context_and_trail.ctx)

    events = await context_and_trail.trail.query(TrailQuery(subject_id=task.id))
    assert len(events) == 1
    assert events[0].payload["task_id"] == task.id


async def test_read_handoff_refuses_a_clearance_above_the_readers_allowance() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    handoff = make_handoff(clearance=HoneyClearance.C2)
    ref = await write_checkpoint(handoff, None, context_and_trail.ctx)

    with pytest.raises(ClearanceError) as exc_info:
        await read_handoff(context_and_trail.ctx.store, ref, HoneyClearance.C1)

    assert exc_info.value.item_clearance is HoneyClearance.C2
    assert exc_info.value.allowance is HoneyClearance.C1


async def test_read_handoff_unknown_ref_raises_handoff_not_found() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    # A well-formed but never-written event id, so HandoffRef itself validates fine and only the
    # store lookup fails.
    never_written_ref = HandoffRef(
        event_id=new_event_id(clock), written_at=clock.now(), clearance=WireHoneyClearance.C1
    )

    store: MemoryStore = context_and_trail.ctx.store
    with pytest.raises(HandoffNotFoundError):
        await read_handoff(store, never_written_ref, HoneyClearance.C2)
