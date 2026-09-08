"""Contract suite for PheromoneTrail and SegmentPurge: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.pheromone.trail.PheromoneTrail contract and runs against both implementations that
    ship: hivemind.pheromone.memory.MemoryPheromoneTrail and hivemind.pheromone.sqlite.
    SqlitePheromoneTrail (over a tmp_path SQLite file, created through SqlitePheromoneTrail.
    create). A second suite below covers hivemind.pheromone.retention.SegmentPurge the same way,
    over MemorySegmentPurge and SqliteSegmentPurge. A new implementation of either protocol joins
    the matching fixture's params and must pass here before it is used anywhere else (codingrules
    14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.trail for the PheromoneTrail protocol under test.
    - hivemind.pheromone.retention for the SegmentPurge protocol under test.
    - packages/waggle/tests/contracts/test_transport_contract.py for the pattern this mirrors
      (this suite needs no by-path harness loader: both implementations import cleanly).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.common.sqlite import connect
from hivemind.pheromone.errors import DuplicateEventError
from hivemind.pheromone.events import CellEvent, PheromoneEvent, TaskEvent
from hivemind.pheromone.memory import MemoryPheromoneTrail
from hivemind.pheromone.retention import MemorySegmentPurge, SegmentPurge, SqliteSegmentPurge
from hivemind.pheromone.sqlite import SqlitePheromoneTrail
from hivemind.pheromone.trail import PheromoneTrail, TrailQuery, TrailSegment
from waggle.clock import FakeClock
from waggle.ids import NodeId, new_cell_id, new_event_id, new_hive_id, new_node_id, new_task_id

_TRAIL_KINDS = ("memory", "sqlite")


def _make_cell_event(clock: FakeClock, node_id: NodeId, **overrides: object) -> CellEvent:
    """Build a well-formed CellEvent on `node_id`, minting fresh ids unless overridden."""
    kwargs: dict[str, object] = {
        "id": new_event_id(clock),
        "hive_id": new_hive_id(clock),
        "node_id": node_id,
        "at": clock.now(),
        "actor": "system",
        "kind": "cell.provisioned",
        "subject_id": new_cell_id(clock),
        "payload": {},
    }
    kwargs.update(overrides)
    return CellEvent(**kwargs)


def _make_task_event(clock: FakeClock, node_id: NodeId, **overrides: object) -> TaskEvent:
    """Build a well-formed TaskEvent on `node_id`, minting fresh ids unless overridden."""
    kwargs: dict[str, object] = {
        "id": new_event_id(clock),
        "hive_id": new_hive_id(clock),
        "node_id": node_id,
        "at": clock.now(),
        "actor": "system",
        "kind": "task.submitted",
        "subject_id": new_task_id(clock),
        "payload": {},
    }
    kwargs.update(overrides)
    return TaskEvent(**kwargs)


async def _seed_interleaved_events(
    trail: PheromoneTrail, clock: FakeClock, node_a: NodeId, node_b: NodeId
) -> list[PheromoneEvent]:
    """Record six events alternating between two nodes, each one tick after the last.

    Returns them in the order recorded, which is also trail order: every `at` strictly
    increases, so (at, node_id, id) never needs a tiebreak.
    """
    events: list[PheromoneEvent] = []
    for _ in range(3):
        events.append(_make_cell_event(clock, node_a))
        clock.advance(1)
        events.append(_make_task_event(clock, node_b))
        clock.advance(1)
    for event in events:
        await trail.record(event)
    return events


@pytest.fixture(params=_TRAIL_KINDS)
async def trail(request: pytest.FixtureRequest, tmp_path: Path) -> PheromoneTrail:
    """A PheromoneTrail of the parametrised kind, the SQLite one on a fresh tmp_path file."""
    clock = FakeClock()
    if request.param == "memory":
        return MemoryPheromoneTrail(clock)
    connection = connect(tmp_path / "hive.sqlite3")
    return await SqlitePheromoneTrail.create(connection, clock)


@dataclass(frozen=True, slots=True)
class _Dataset:
    """Six interleaved events already recorded on a `trail` fixture, plus their two node ids."""

    node_a: NodeId
    node_b: NodeId
    events: list[PheromoneEvent]


@pytest.fixture
async def dataset(trail: PheromoneTrail) -> _Dataset:
    """Seed `trail` with `_seed_interleaved_events` and hand back the events and node ids."""
    clock = FakeClock()
    node_a = new_node_id(clock)
    node_b = new_node_id(clock)
    events = await _seed_interleaved_events(trail, clock, node_a, node_b)
    return _Dataset(node_a=node_a, node_b=node_b, events=events)


# ──────────────────────────────────────────────────────────────────────────────
# record / query: the basics
# ──────────────────────────────────────────────────────────────────────────────


async def test_record_then_query_returns_the_exact_event_with_its_subclass(
    trail: PheromoneTrail,
) -> None:
    clock = FakeClock()
    event = _make_cell_event(clock, new_node_id(clock))

    await trail.record(event)
    results = await trail.query(TrailQuery())

    assert results == (event,)
    assert isinstance(results[0], CellEvent)


async def test_record_a_known_id_raises_and_leaves_exactly_one_row(trail: PheromoneTrail) -> None:
    clock = FakeClock()
    event = _make_cell_event(clock, new_node_id(clock))
    await trail.record(event)

    with pytest.raises(DuplicateEventError):
        await trail.record(event)

    assert await trail.query(TrailQuery()) == (event,)


# ──────────────────────────────────────────────────────────────────────────────
# query: trail order and filters
# ──────────────────────────────────────────────────────────────────────────────


async def test_query_orders_events_across_two_nodes_by_trail_order(
    trail: PheromoneTrail, dataset: _Dataset
) -> None:
    results = await trail.query(TrailQuery())

    assert list(results) == dataset.events


async def test_query_newest_first_reverses_trail_order_end_to_end(
    trail: PheromoneTrail, dataset: _Dataset
) -> None:
    results = await trail.query(TrailQuery(newest_first=True))

    assert list(results) == list(reversed(dataset.events))


async def test_query_since_and_until_are_both_inclusive(
    trail: PheromoneTrail, dataset: _Dataset
) -> None:
    since = dataset.events[1].at
    until = dataset.events[4].at

    results = await trail.query(TrailQuery(since=since, until=until))

    assert list(results) == dataset.events[1:5]


async def test_query_filters_by_family(trail: PheromoneTrail, dataset: _Dataset) -> None:
    results = await trail.query(TrailQuery(family="task"))

    assert results == tuple(event for event in dataset.events if event.family == "task")


async def test_query_filters_by_kind(trail: PheromoneTrail, dataset: _Dataset) -> None:
    results = await trail.query(TrailQuery(kind="cell.provisioned"))

    assert results == tuple(event for event in dataset.events if event.kind == "cell.provisioned")


async def test_query_filters_by_subject_id(trail: PheromoneTrail, dataset: _Dataset) -> None:
    target = dataset.events[2]

    results = await trail.query(TrailQuery(subject_id=target.subject_id))

    assert results == (target,)


async def test_query_filters_by_node_id(trail: PheromoneTrail, dataset: _Dataset) -> None:
    results = await trail.query(TrailQuery(node_id=dataset.node_a))

    assert results == tuple(event for event in dataset.events if event.node_id == dataset.node_a)


async def test_query_truncates_to_limit(trail: PheromoneTrail, dataset: _Dataset) -> None:
    results = await trail.query(TrailQuery(limit=2))

    assert list(results) == dataset.events[:2]


# ──────────────────────────────────────────────────────────────────────────────
# export_segment
# ──────────────────────────────────────────────────────────────────────────────


async def test_export_segment_returns_only_that_nodes_events_at_or_after_since(
    trail: PheromoneTrail, dataset: _Dataset
) -> None:
    node_a_events = [event for event in dataset.events if event.node_id == dataset.node_a]
    since = node_a_events[1].at

    segment = await trail.export_segment(dataset.node_a, since=since)

    assert segment.node_id == dataset.node_a
    assert list(segment.events) == [event for event in node_a_events if event.at >= since]


async def test_export_segment_json_round_trip_preserves_event_subclasses(
    trail: PheromoneTrail,
) -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    await trail.record(_make_cell_event(clock, node_id))
    await trail.record(_make_task_event(clock, node_id))

    segment = await trail.export_segment(node_id)
    restored = TrailSegment.model_validate_json(segment.model_dump_json())

    assert restored == segment
    assert {type(event) for event in restored.events} == {CellEvent, TaskEvent}


# ──────────────────────────────────────────────────────────────────────────────
# merge_segment
# ──────────────────────────────────────────────────────────────────────────────


async def test_merge_two_node_segments_with_interleaved_timestamps_yields_one_ordered_log(
    trail: PheromoneTrail,
) -> None:
    clock = FakeClock()
    node_a = new_node_id(clock)
    node_b = new_node_id(clock)
    event_a1 = _make_cell_event(clock, node_a)
    clock.advance(1)
    event_b1 = _make_cell_event(clock, node_b)
    clock.advance(1)
    event_a2 = _make_cell_event(clock, node_a)
    clock.advance(1)
    event_b2 = _make_cell_event(clock, node_b)
    segment_a = TrailSegment(node_id=node_a, exported_at=clock.now(), events=(event_a1, event_a2))
    segment_b = TrailSegment(node_id=node_b, exported_at=clock.now(), events=(event_b1, event_b2))

    inserted_a = await trail.merge_segment(segment_a)
    inserted_b = await trail.merge_segment(segment_b)

    assert (inserted_a, inserted_b) == (2, 2)
    results = await trail.query(TrailQuery())
    assert list(results) == [event_a1, event_b1, event_a2, event_b2]


async def test_merge_segment_twice_inserts_nothing_the_second_time(trail: PheromoneTrail) -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    event = _make_cell_event(clock, node_id)
    segment = TrailSegment(node_id=node_id, exported_at=clock.now(), events=(event,))
    await trail.merge_segment(segment)

    second = await trail.merge_segment(segment)

    assert second == 0
    assert await trail.query(TrailQuery()) == (event,)


def test_a_segment_carrying_an_event_from_another_node_is_rejected_at_construction() -> None:
    clock = FakeClock()
    node_a = new_node_id(clock)
    foreign_event = _make_cell_event(clock, new_node_id(clock))

    with pytest.raises(ValidationError):
        TrailSegment(node_id=node_a, exported_at=clock.now(), events=(foreign_event,))


# ──────────────────────────────────────────────────────────────────────────────
# SegmentPurge contract
# ──────────────────────────────────────────────────────────────────────────────

_PurgePair = tuple[SegmentPurge, PheromoneTrail]


@pytest.fixture(params=_TRAIL_KINDS)
async def segment_purge_pair(request: pytest.FixtureRequest, tmp_path: Path) -> _PurgePair:
    """A (SegmentPurge, PheromoneTrail) pair of the parametrised kind, sharing one store."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return MemorySegmentPurge(memory_trail), memory_trail
    connection = connect(tmp_path / "hive.sqlite3")
    sqlite_trail = await SqlitePheromoneTrail.create(connection, clock)
    return SqliteSegmentPurge(connection), sqlite_trail


async def test_purge_segment_removes_exactly_that_nodes_events(
    segment_purge_pair: _PurgePair,
) -> None:
    segments, store = segment_purge_pair
    clock = FakeClock()
    kept_node = new_node_id(clock)
    dropped_node = new_node_id(clock)
    kept_event = _make_cell_event(clock, kept_node)
    await store.record(kept_event)
    await store.record(_make_cell_event(clock, dropped_node))

    removed = await segments.purge_segment(dropped_node)

    assert removed == 1
    assert await store.query(TrailQuery()) == (kept_event,)


async def test_purge_segment_a_second_time_removes_nothing(
    segment_purge_pair: _PurgePair,
) -> None:
    segments, store = segment_purge_pair
    clock = FakeClock()
    node_id = new_node_id(clock)
    await store.record(_make_cell_event(clock, node_id))
    await segments.purge_segment(node_id)

    second = await segments.purge_segment(node_id)

    assert second == 0
