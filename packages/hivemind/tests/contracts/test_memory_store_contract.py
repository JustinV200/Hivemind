"""Contract suite for MemoryStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.memory.store.protocol.MemoryStore contract and runs against both implementations
    that ship: hivemind.memory.store.memory.InMemoryMemoryStore (over MemoryPheromoneTrail) and
    hivemind.memory.store.sqlite.SqliteMemoryStore (over SqlitePheromoneTrail, both on the same
    tmp_path SQLite file, with the Pheromone Trail's own migrations applied first). A new
    implementation joins the fixture's params and must pass here before it is used anywhere else
    (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.store.protocol for the MemoryStore protocol under test.
    - packages/hivemind/tests/contracts/test_task_store_contract.py for the pattern this mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.memory import make_episode, make_handoff, make_note, make_pin

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.memory.errors import HandoffNotFoundError
from hivemind.memory.notes import MAX_NOTES_PER_AUTHOR
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.memory.store.protocol import MemoryStore
from hivemind.memory.store.sqlite import SqliteMemoryStore
from hivemind.pheromone import (
    MemoryEvent,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
)
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

_STORE_KINDS = ("memory", "sqlite")


def _make_memory_event(clock: FakeClock, subject_id: str, kind: str) -> MemoryEvent:
    """Build a well-formed MemoryEvent whose subject is `subject_id`, minting a fresh id/node."""
    return MemoryEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


@dataclass(frozen=True, slots=True)
class _StoreAndTrail:
    """A MemoryStore and the PheromoneTrail it records events on, for the same fixture kind."""

    store: MemoryStore
    trail: PheromoneTrail


@pytest.fixture(params=_STORE_KINDS)
async def store_and_trail(request: pytest.FixtureRequest, tmp_path: Path) -> _StoreAndTrail:
    """A (MemoryStore, PheromoneTrail) pair of the parametrised kind, sharing one clock and file."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _StoreAndTrail(store=InMemoryMemoryStore(memory_trail), trail=memory_trail)
    # SQLite: two connections to the same file (ADR-0006 decision 1: two stores that write the
    # same file use separate connections); the trail's migration must run before the store's own.
    db_path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db_path), clock)
    sqlite_store = await SqliteMemoryStore.create(connect(db_path), clock)
    return _StoreAndTrail(store=sqlite_store, trail=sqlite_trail)


# ──────────────────────────────────────────────────────────────────────────────
# Pins
# ──────────────────────────────────────────────────────────────────────────────


async def test_add_pin_then_list_pins_returns_it(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    pin = make_pin(clock=clock)

    await store_and_trail.store.add_pin(pin, _make_memory_event(clock, pin.id, "memory.pinned"))
    results = await store_and_trail.store.list_pins(HoneyClearance.C1)

    assert results == (pin,)


async def test_add_pin_records_its_event_on_the_trail(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    pin = make_pin(clock=clock)
    event = _make_memory_event(clock, pin.id, "memory.pinned")

    await store_and_trail.store.add_pin(pin, event)

    assert await store_and_trail.trail.query(TrailQuery(subject_id=pin.id)) == (event,)


async def test_list_pins_filters_by_allowance(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    c1_pin = make_pin(clock=clock, clearance=HoneyClearance.C1)
    c2_pin = make_pin(clock=clock, clearance=HoneyClearance.C2)
    await store_and_trail.store.add_pin(
        c1_pin, _make_memory_event(clock, c1_pin.id, "memory.pinned")
    )
    await store_and_trail.store.add_pin(
        c2_pin, _make_memory_event(clock, c2_pin.id, "memory.pinned")
    )

    results = await store_and_trail.store.list_pins(HoneyClearance.C1)

    # A C2 pin never reaches a C1 allowance: codingrules section 8.9.
    assert results == (c1_pin,)


async def test_remove_pin_removes_it_and_is_idempotent(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    pin = make_pin(clock=clock)
    await store_and_trail.store.add_pin(pin, _make_memory_event(clock, pin.id, "memory.pinned"))

    await store_and_trail.store.remove_pin(pin.id)
    await store_and_trail.store.remove_pin(pin.id)  # Removing an unknown id is a no-op.

    assert await store_and_trail.store.list_pins(HoneyClearance.C2) == ()


# ──────────────────────────────────────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────────────────────────────────────


async def test_add_note_then_list_notes_returns_it(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    note = make_note(clock=clock)

    await store_and_trail.store.add_note(note, _make_memory_event(clock, note.id, "memory.note"))
    results = await store_and_trail.store.list_notes(None, HoneyClearance.C1, 10)

    assert results == (note,)


async def test_list_notes_filters_by_author_and_allowance(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    mine = make_note(clock=clock, author="worker_mine", clearance=HoneyClearance.C1)
    theirs = make_note(clock=clock, author="worker_theirs", clearance=HoneyClearance.C1)
    royal = make_note(clock=clock, author="worker_mine", clearance=HoneyClearance.C2)
    for note in (mine, theirs, royal):
        await store_and_trail.store.add_note(
            note, _make_memory_event(clock, note.id, "memory.note")
        )

    only_mine = await store_and_trail.store.list_notes("worker_mine", HoneyClearance.C1, 10)

    assert only_mine == (mine,)


async def test_add_note_evicts_the_oldest_past_the_per_author_bound(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    author = "worker_prolific"
    notes = []
    for _ in range(MAX_NOTES_PER_AUTHOR + 1):
        note = make_note(clock=clock, author=author)
        await store_and_trail.store.add_note(
            note, _make_memory_event(clock, note.id, "memory.note")
        )
        notes.append(note)
        clock.advance(1)

    results = await store_and_trail.store.list_notes(author, HoneyClearance.C2, 1_000)

    # The very first note written is the oldest, and must be the one evicted.
    assert len(results) == MAX_NOTES_PER_AUTHOR
    assert notes[0].id not in {note.id for note in results}
    assert notes[-1].id in {note.id for note in results}


# ──────────────────────────────────────────────────────────────────────────────
# Handoffs
# ──────────────────────────────────────────────────────────────────────────────


async def test_put_handoff_then_get_handoff_round_trips(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    handoff = make_handoff(clearance=HoneyClearance.C1)
    event_id = new_event_id(clock)
    event = _make_memory_event(clock, event_id, "memory.checkpoint")

    await store_and_trail.store.put_handoff(event_id, handoff, None, event)
    result, clearance = await store_and_trail.store.get_handoff(event_id)

    assert result == handoff
    assert clearance == HoneyClearance.C1


async def test_put_handoff_records_its_event_on_the_trail(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    handoff = make_handoff()
    event_id = new_event_id(clock)
    event = _make_memory_event(clock, event_id, "memory.checkpoint")

    await store_and_trail.store.put_handoff(event_id, handoff, None, event)

    assert await store_and_trail.trail.query(TrailQuery(subject_id=event_id)) == (event,)


async def test_get_handoff_unknown_id_raises_handoff_not_found(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()

    with pytest.raises(HandoffNotFoundError):
        await store_and_trail.store.get_handoff(new_event_id(clock))


# ──────────────────────────────────────────────────────────────────────────────
# Episodes
# ──────────────────────────────────────────────────────────────────────────────


async def test_put_episode_then_list_episodes_returns_it(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    episode = make_episode(clock=clock)

    await store_and_trail.store.put_episode(
        episode, _make_memory_event(clock, episode.id, "memory.episode")
    )
    results = await store_and_trail.store.list_episodes(None, HoneyClearance.C1, 10)

    assert results == (episode,)


async def test_list_episodes_orders_newest_first_and_filters(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    first = make_episode(clock=clock, principal="warden_a")
    clock.advance(1)
    second = make_episode(clock=clock, principal="warden_a")
    clock.advance(1)
    other = make_episode(clock=clock, principal="warden_b")
    for episode in (first, second, other):
        await store_and_trail.store.put_episode(
            episode, _make_memory_event(clock, episode.id, "memory.episode")
        )

    only_a = await store_and_trail.store.list_episodes("warden_a", HoneyClearance.C2, 10)

    assert [episode.id for episode in only_a] == [second.id, first.id]


async def test_list_episodes_respects_limit(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    for _ in range(3):
        episode = make_episode(clock=clock)
        await store_and_trail.store.put_episode(
            episode, _make_memory_event(clock, episode.id, "memory.episode")
        )
        clock.advance(1)

    results = await store_and_trail.store.list_episodes(None, HoneyClearance.C2, 2)

    assert len(results) == 2


async def test_purge_episodes_before_removes_older_ones_and_returns_the_count(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    old = make_episode(clock=clock)
    await store_and_trail.store.put_episode(
        old, _make_memory_event(clock, old.id, "memory.episode")
    )
    clock.advance(1)
    cutoff = clock.now()
    clock.advance(1)
    recent = make_episode(clock=clock)
    await store_and_trail.store.put_episode(
        recent, _make_memory_event(clock, recent.id, "memory.episode")
    )

    removed = await store_and_trail.store.purge_episodes_before(cutoff)

    assert removed == 1
    remaining = await store_and_trail.store.list_episodes(None, HoneyClearance.C2, 10)
    assert [episode.id for episode in remaining] == [recent.id]
