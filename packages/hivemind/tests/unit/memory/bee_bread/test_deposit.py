"""Tests for hivemind.memory.bee_bread.deposit: every write path into Bee Bread.

Fits into the Hive:
    Mirrors src/hivemind/memory/bee_bread/deposit.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.bee_bread.deposit for the module under test.
"""

from __future__ import annotations

import pytest
from builders.memory import make_alarm_summary, make_pin, make_task_summary

from hivemind.cell import HoneyClearance
from hivemind.common.errors import InvariantViolationError
from hivemind.memory.bee_bread.deposit import (
    deposit_handoff_ref,
    deposit_hot_state_item,
    deposit_tool_result,
    deposit_transcript,
)
from hivemind.memory.bee_bread.entry import BeeBreadEntryKind
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_task_id


def _ctx_and_trail(clock: FakeClock) -> tuple[MemoryContext, PheromoneTrail]:
    """Build a MemoryContext plus the same trail its store records events on."""
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryMemoryStore(trail)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return MemoryContext(store=store, identity=identity, clock=clock), trail


def _ctx(clock: FakeClock) -> MemoryContext:
    """Build a MemoryContext alone, for tests that do not need the trail directly."""
    return _ctx_and_trail(clock)[0]


async def test_deposit_transcript_stores_the_full_text_as_payload() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    task_id = new_task_id(clock)

    entry = await deposit_transcript("the whole transcript", task_id, HoneyClearance.C1, ctx)

    assert entry.kind == BeeBreadEntryKind.TRANSCRIPT
    assert entry.payload == "the whole transcript"
    assert entry.task_id == task_id
    fetched = await ctx.store.get_bee_bread_entry(entry.id, HoneyClearance.C1)
    assert fetched == entry


async def test_deposit_transcript_records_its_trail_event() -> None:
    clock = FakeClock()
    ctx, trail = _ctx_and_trail(clock)

    entry = await deposit_transcript("text", None, HoneyClearance.C1, ctx)

    events = await trail.query(TrailQuery(subject_id=entry.id))
    assert len(events) == 1
    assert events[0].kind == "memory.bee_bread_deposited"


async def test_deposit_tool_result_stores_the_full_text_as_payload() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)

    entry = await deposit_tool_result("a huge tool result", None, HoneyClearance.C1, ctx)

    assert entry.kind == BeeBreadEntryKind.TOOL_RESULT
    assert entry.payload == "a huge tool result"


async def test_deposit_handoff_ref_indexes_by_event_id_only() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    handoff_event_id = new_event_id(clock)
    task_id = new_task_id(clock)

    entry = await deposit_handoff_ref(handoff_event_id, task_id, HoneyClearance.C1, ctx)

    assert entry.kind == BeeBreadEntryKind.HANDOFF
    assert entry.ref_ids == (handoff_event_id,)
    assert entry.task_id == task_id
    assert entry.payload is None


async def test_deposit_hot_state_item_archives_a_task_summary() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    task = make_task_summary(clock=clock)

    entry = await deposit_hot_state_item(task, ctx)

    assert entry.kind == BeeBreadEntryKind.TASK_HISTORY
    assert entry.ref_ids == (task.id,)
    assert entry.task_id == task.id
    assert entry.text == task.title
    assert entry.clearance == task.clearance


async def test_deposit_hot_state_item_archives_an_alarm_summary() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    alarm = make_alarm_summary(clock=clock)

    entry = await deposit_hot_state_item(alarm, ctx)

    assert entry.kind == BeeBreadEntryKind.TRAIL_EVENT
    assert entry.ref_ids == (alarm.id,)
    assert entry.text == alarm.detail


async def test_deposit_hot_state_item_raises_for_a_pin() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    pin = make_pin(clock=clock)

    with pytest.raises(InvariantViolationError):
        await deposit_hot_state_item(pin, ctx)
