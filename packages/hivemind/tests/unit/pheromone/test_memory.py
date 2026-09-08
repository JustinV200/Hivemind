"""Tests for hivemind.pheromone.memory: MemoryPheromoneTrail.drop_segment.

The rest of MemoryPheromoneTrail's behaviour (record, query, export_segment, merge_segment) is
covered by tests/contracts/test_pheromone_trail_contract.py, which runs it against the same
PheromoneTrail contract as SqlitePheromoneTrail. This module covers drop_segment, the one method
outside that Protocol (used only by hivemind.pheromone.retention.MemorySegmentPurge).

Fits into the Hive:
    Mirrors src/hivemind/pheromone/memory.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.memory for the module under test.
    - tests/contracts/test_pheromone_trail_contract.py for the shared PheromoneTrail behaviour.
"""

from __future__ import annotations

from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail import TrailQuery
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


async def test_drop_segment_removes_only_the_named_nodes_events() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    kept_node = new_node_id(clock)
    dropped_node = new_node_id(clock)
    await trail.record(_make_cell_event(clock, kept_node))
    await trail.record(_make_cell_event(clock, dropped_node))

    removed = trail.drop_segment(dropped_node)

    assert removed == 1
    remaining = await trail.query(TrailQuery())
    assert [event.node_id for event in remaining] == [kept_node]


async def test_drop_segment_on_an_unknown_node_removes_nothing() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    await trail.record(_make_cell_event(clock, new_node_id(clock)))

    removed = trail.drop_segment(new_node_id(clock))

    assert removed == 0
    assert len(await trail.query(TrailQuery())) == 1


async def test_drop_segment_twice_returns_zero_the_second_time() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    node_id = new_node_id(clock)
    await trail.record(_make_cell_event(clock, node_id))
    first = trail.drop_segment(node_id)

    second = trail.drop_segment(node_id)

    assert first == 1
    assert second == 0
