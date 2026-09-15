"""Tests for hivemind.memory.bee_bread.index: BeeBread, the lookup-only warm-tier reader.

Fits into the Hive:
    Mirrors src/hivemind/memory/bee_bread/index.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.bee_bread.index for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.memory import make_bee_bread_entry

from hivemind.cell import HoneyClearance
from hivemind.memory.bee_bread.index import BeeBread
from hivemind.memory.errors import BeeBreadEntryNotFoundError, ClearanceError
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_task_id


def _event(clock: FakeClock, subject_id: str) -> MemoryEvent:
    return MemoryEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="memory.bee_bread_deposited",
        subject_id=subject_id,
        payload={},
    )


async def test_by_id_returns_a_deposited_entry() -> None:
    clock = FakeClock()
    store = InMemoryMemoryStore(MemoryPheromoneTrail(clock))
    entry = make_bee_bread_entry(clock=clock)
    await store.add_bee_bread_entry(entry, _event(clock, entry.id))
    bee_bread = BeeBread(store)

    result = await bee_bread.by_id(entry.id, HoneyClearance.C1)

    assert result == entry


async def test_by_id_unknown_id_raises_not_found() -> None:
    clock = FakeClock()
    store = InMemoryMemoryStore(MemoryPheromoneTrail(clock))
    bee_bread = BeeBread(store)

    with pytest.raises(BeeBreadEntryNotFoundError):
        await bee_bread.by_id(new_event_id(clock), HoneyClearance.C2)


async def test_by_id_refuses_an_entry_above_the_readers_allowance() -> None:
    clock = FakeClock()
    store = InMemoryMemoryStore(MemoryPheromoneTrail(clock))
    entry = make_bee_bread_entry(clock=clock, clearance=HoneyClearance.C2)
    await store.add_bee_bread_entry(entry, _event(clock, entry.id))
    bee_bread = BeeBread(store)

    with pytest.raises(ClearanceError):
        await bee_bread.by_id(entry.id, HoneyClearance.C1)


async def test_by_task_returns_only_entries_for_that_task() -> None:
    clock = FakeClock()
    store = InMemoryMemoryStore(MemoryPheromoneTrail(clock))
    task_id = new_task_id(clock)
    mine = make_bee_bread_entry(clock=clock, task_id=task_id, ref_ids=(task_id,))
    other = make_bee_bread_entry(clock=clock)
    for entry in (mine, other):
        await store.add_bee_bread_entry(entry, _event(clock, entry.id))
    bee_bread = BeeBread(store)

    results = await bee_bread.by_task(task_id, HoneyClearance.C2)

    assert results == (mine,)


async def test_between_returns_only_entries_in_range() -> None:
    clock = FakeClock()
    store = InMemoryMemoryStore(MemoryPheromoneTrail(clock))
    early = make_bee_bread_entry(clock=clock)
    clock.advance(3600)
    cutoff = clock.now()
    clock.advance(3600)
    late = make_bee_bread_entry(clock=clock)
    for entry in (early, late):
        await store.add_bee_bread_entry(entry, _event(clock, entry.id))
    bee_bread = BeeBread(store)

    results = await bee_bread.between(
        cutoff - timedelta(hours=1), cutoff + timedelta(seconds=1), HoneyClearance.C2
    )

    assert results == (early,)
