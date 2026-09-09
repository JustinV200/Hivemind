"""Tests for hivemind.pheromone.trail: TrailQuery, TrailSegment and TRAIL_ORDER_KEY.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/trail.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.trail for the module under test.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.trail import (
    DEFAULT_QUERY_LIMIT,
    MAX_QUERY_LIMIT,
    TRAIL_ORDER_KEY,
    TrailQuery,
    TrailSegment,
)
from waggle.clock import FakeClock
from waggle.ids import NodeId, new_cell_id, new_event_id, new_hive_id, new_node_id


def _make_cell_event(clock: FakeClock, node_id: NodeId) -> CellEvent:
    """Build a well-formed CellEvent recorded on `node_id`."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=node_id,
        at=clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=new_cell_id(clock),
        payload={},
    )


# ──────────────────────────────────────────────────────────────────────────────
# TRAIL_ORDER_KEY
# ──────────────────────────────────────────────────────────────────────────────


def test_trail_order_key_is_at_then_node_id_with_insertion_order_breaking_ties() -> None:
    assert TRAIL_ORDER_KEY == ("at", "node_id")


# ──────────────────────────────────────────────────────────────────────────────
# TrailQuery
# ──────────────────────────────────────────────────────────────────────────────


def test_trail_query_defaults_are_the_documented_constants() -> None:
    query = TrailQuery()

    assert query.limit == DEFAULT_QUERY_LIMIT
    assert query.newest_first is False
    assert query.since is None
    assert query.until is None
    assert query.family is None
    assert query.kind is None
    assert query.subject_id is None
    assert query.node_id is None


def test_trail_query_accepts_limit_at_both_bounds() -> None:
    assert TrailQuery(limit=1).limit == 1
    assert TrailQuery(limit=MAX_QUERY_LIMIT).limit == MAX_QUERY_LIMIT


@pytest.mark.parametrize("limit", [0, MAX_QUERY_LIMIT + 1])
def test_trail_query_rejects_a_limit_outside_the_bounds(limit: int) -> None:
    with pytest.raises(ValidationError):
        TrailQuery(limit=limit)


def test_trail_query_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TrailQuery(bogus="nope")  # type: ignore[call-arg]


def test_trail_query_rejects_a_naive_since() -> None:
    with pytest.raises(ValidationError):
        TrailQuery(since=datetime(2024, 1, 1))


def test_trail_query_round_trips_through_json() -> None:
    clock = FakeClock()
    query = TrailQuery(since=clock.now(), family="cell", limit=5, newest_first=True)

    restored = TrailQuery.model_validate_json(query.model_dump_json())

    assert restored == query


# ──────────────────────────────────────────────────────────────────────────────
# TrailSegment
# ──────────────────────────────────────────────────────────────────────────────


def test_trail_segment_round_trips_through_json_preserving_the_event_subclass() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    event = _make_cell_event(clock, node_id)
    segment = TrailSegment(node_id=node_id, exported_at=clock.now(), events=(event,))

    restored = TrailSegment.model_validate_json(segment.model_dump_json())

    assert restored == segment
    assert isinstance(restored.events[0], CellEvent)


def test_trail_segment_accepts_events_given_as_plain_mappings() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    event = _make_cell_event(clock, node_id)
    raw_event = event.model_dump(mode="json")

    segment = TrailSegment(node_id=node_id, exported_at=clock.now(), events=(raw_event,))

    assert segment.events == (event,)
    assert isinstance(segment.events[0], CellEvent)


def test_trail_segment_rejects_an_event_from_another_node() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    foreign_event = _make_cell_event(clock, new_node_id(clock))

    with pytest.raises(ValidationError):
        TrailSegment(node_id=node_id, exported_at=clock.now(), events=(foreign_event,))


def test_trail_segment_rejects_a_non_sequence_events_value() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)

    with pytest.raises(ValidationError):
        TrailSegment(node_id=node_id, exported_at=clock.now(), events=42)


def test_trail_segment_accepts_an_empty_event_tuple() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)

    segment = TrailSegment(node_id=node_id, exported_at=clock.now(), events=())

    assert segment.events == ()


def test_trail_segment_rejects_an_unknown_field() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    kwargs = {"node_id": node_id, "exported_at": clock.now(), "events": (), "bogus": "nope"}

    with pytest.raises(ValidationError):
        TrailSegment(**kwargs)
