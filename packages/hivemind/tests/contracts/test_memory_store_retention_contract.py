"""Contract suite for MemoryStore's Bee Bread retention clauses, over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Runs the `list_bee_bread_of_kind` and
    `purge_bee_bread_before` clauses of the hivemind.memory.store.protocol.MemoryStore contract
    (roadmap step 11.10: a watch observation lives for a retention window) over the two
    implementations that ship, as tests.contracts.test_memory_store_contract does for the rest:
    a read narrows to one kind inside the store, a purge removes one kind older than a cutoff and
    nothing else.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.store.retention and hivemind.memory.store.sqlite.retention for the two
      halves under test.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from builders.memory import make_bee_bread_entry

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.memory import BeeBreadEntry, BeeBreadEntryKind
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.memory.store.protocol import MemoryStore
from hivemind.memory.store.sqlite import SqliteMemoryStore
from hivemind.memory.taint import TaintedKind, TaintMarker, TaintSource, TaintState, TaintTarget
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail, SqlitePheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

_OBSERVATION = BeeBreadEntryKind.WATCH_OBSERVATION


@pytest.fixture(params=("memory", "sqlite"))
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> MemoryStore:
    """A MemoryStore of the parametrised kind; SQLite on one tmp_path file, trail first."""
    clock = FakeClock()
    if request.param == "memory":
        return InMemoryMemoryStore(MemoryPheromoneTrail(clock))
    db_path = tmp_path / "hive.sqlite3"
    await SqlitePheromoneTrail.create(connect(db_path), clock)
    return await SqliteMemoryStore.create(connect(db_path), clock)


def _event(clock: FakeClock, entry: BeeBreadEntry) -> MemoryEvent:
    """Build the memory.bee_bread_deposited event that accompanies `entry`."""
    return MemoryEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="memory.bee_bread_deposited",
        subject_id=entry.id,
        payload={"entry_kind": entry.kind.value},
    )


async def _add(
    store: MemoryStore, clock: FakeClock, kind: BeeBreadEntryKind, **overrides: object
) -> BeeBreadEntry:
    """Add one entry of `kind` stamped at the clock's present, then step the clock a second."""
    fields: dict[str, object] = {"kind": kind, "task_id": None, "ref_ids": (), "text": None}
    fields["payload"] = "{}"
    fields.update(overrides)
    entry = make_bee_bread_entry(clock=clock, **fields)
    await store.add_bee_bread_entry(entry, _event(clock, entry))
    clock.advance(1)
    return entry


async def test_list_of_kind_returns_only_that_kind_in_the_window_oldest_first(
    store: MemoryStore,
) -> None:
    clock = FakeClock()
    first = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)
    await _add(store, clock, BeeBreadEntryKind.TRANSCRIPT)  # Another kind in the same window.
    second = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)

    found = await store.list_bee_bread_of_kind(
        _OBSERVATION, first.created_at, second.created_at, HoneyClearance.C2
    )

    assert [entry.id for entry in found] == [first.id, second.id]


async def test_list_of_kind_bounds_are_inclusive_and_outside_entries_are_left_out(
    store: MemoryStore,
) -> None:
    clock = FakeClock()
    before = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)
    inside = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)
    after = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)

    found = await store.list_bee_bread_of_kind(
        _OBSERVATION, inside.created_at, inside.created_at, HoneyClearance.C2
    )

    assert [entry.id for entry in found] == [inside.id]
    assert before.created_at < inside.created_at < after.created_at


async def test_list_of_kind_hides_what_the_reader_may_not_see(store: MemoryStore) -> None:
    clock = FakeClock()
    entry = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)
    window = (entry.created_at - timedelta(seconds=1), entry.created_at + timedelta(seconds=1))

    below = await store.list_bee_bread_of_kind(_OBSERVATION, *window, HoneyClearance.C1)
    at = await store.list_bee_bread_of_kind(_OBSERVATION, *window, HoneyClearance.C2)

    assert below == ()
    assert at == (entry,)


async def test_list_of_kind_never_returns_a_tainted_entry(store: MemoryStore) -> None:
    clock = FakeClock()
    entry = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)
    marker = TaintMarker(
        state=TaintState.TAINTED,
        source=TaintSource.QUARANTINE,
        reason="Taken out of prompts.",
        event_id=new_event_id(clock),
        at=clock.now(),
    )
    target = TaintTarget(kind=TaintedKind.BEE_BREAD, item_id=entry.id)
    await store.write_taint(target, marker, _event(clock, entry))

    found = await store.list_bee_bread_of_kind(
        _OBSERVATION, entry.created_at, entry.created_at, HoneyClearance.C2
    )

    assert found == ()


async def test_purge_removes_only_that_kind_older_than_the_cutoff(store: MemoryStore) -> None:
    clock = FakeClock()
    old = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)
    other_kind = await _add(store, clock, BeeBreadEntryKind.TRANSCRIPT)
    fresh = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)

    removed = await store.purge_bee_bread_before(_OBSERVATION, fresh.created_at)

    assert removed == 1
    kept = await store.list_bee_bread_between(old.created_at, fresh.created_at, HoneyClearance.C2)
    assert [entry.id for entry in kept] == [other_kind.id, fresh.id]


async def test_purge_keeps_an_entry_written_exactly_at_the_cutoff(store: MemoryStore) -> None:
    clock = FakeClock()
    entry = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)

    removed = await store.purge_bee_bread_before(_OBSERVATION, entry.created_at)

    assert removed == 0
    assert await store.get_bee_bread_entry(entry.id, HoneyClearance.C2) == entry


async def test_purge_of_a_kind_with_nothing_older_removes_nothing(store: MemoryStore) -> None:
    clock = FakeClock()
    await _add(store, clock, BeeBreadEntryKind.TRANSCRIPT)

    removed = await store.purge_bee_bread_before(_OBSERVATION, clock.now() + timedelta(days=1))

    assert removed == 0


async def test_a_purged_entry_is_gone_for_good(store: MemoryStore) -> None:
    clock = FakeClock()
    entry = await _add(store, clock, _OBSERVATION, clearance=HoneyClearance.C2)

    await store.purge_bee_bread_before(_OBSERVATION, clock.now())

    window = (entry.created_at, entry.created_at)
    assert await store.list_bee_bread_of_kind(_OBSERVATION, *window, HoneyClearance.C2) == ()
