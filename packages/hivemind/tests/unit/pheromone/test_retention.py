"""Tests for hivemind.pheromone.retention.NightVeilTeardownPurge: the Night Veil boundary.

hivemind.pheromone.retention.SegmentPurge itself (SqliteSegmentPurge, MemorySegmentPurge) has its
own contract suite in tests/contracts/test_pheromone_trail_contract.py; this module covers
NightVeilTeardownPurge's own orchestration -- ordering, counting and the surviving skeleton event
-- with a fake SegmentPurge and fake SideChannelPurgers standing in for the trail segment and the
VPN/Tor side channels.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/retention.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention for the module under test.
"""

from __future__ import annotations

from hivemind.pheromone.memory import MemoryPheromoneTrail
from hivemind.pheromone.retention import NightVeilTeardownPurge, PurgeReport, TrailRecorder
from hivemind.pheromone.trail import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import CellId, NodeId, new_cell_id, new_hive_id, new_node_id


class _FakeSegmentPurge:
    """A SegmentPurge that records that it ran and returns a fixed count."""

    def __init__(self, count: int, calls: list[str]) -> None:
        self._count = count
        self._calls = calls

    async def purge_segment(self, node_id: NodeId) -> int:
        self._calls.append("segment")
        return self._count


class _FakeSideChannelPurger:
    """A SideChannelPurger that records that it ran and returns a fixed count."""

    def __init__(self, count: int, calls: list[str], name: str) -> None:
        self._count = count
        self._calls = calls
        self._name = name

    async def purge(self, cell_id: CellId) -> int:
        self._calls.append(self._name)
        return self._count


def _build_purge(
    segment_count: int, side_channel_counts: list[int], calls: list[str]
) -> tuple[NightVeilTeardownPurge, MemoryPheromoneTrail, FakeClock, NodeId]:
    """Wire a NightVeilTeardownPurge over fakes, sharing `calls` for ordering assertions."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=new_hive_id(clock), node_id=new_node_id(clock)
    )
    segments = _FakeSegmentPurge(segment_count, calls)
    side_channels = [
        _FakeSideChannelPurger(count, calls, name=f"side_{index}")
        for index, count in enumerate(side_channel_counts)
    ]
    purge = NightVeilTeardownPurge(
        segments=segments, side_channels=side_channels, recorder=recorder
    )
    return purge, trail, clock, recorder.node_id


async def test_purge_report_carries_the_segment_and_side_channel_counts() -> None:
    calls: list[str] = []
    purge, _, clock, _ = _build_purge(segment_count=3, side_channel_counts=[2, 5], calls=calls)
    cell_id = new_cell_id(clock)
    segment_node = new_node_id(clock)

    report = await purge.purge(cell_id, segment_node, actor="system")

    assert report == PurgeReport(
        cell_id=cell_id,
        segment_node_id=segment_node,
        events_purged=3,
        side_channel_records_purged=7,
    )


async def test_purge_runs_the_segment_before_every_side_channel_in_order() -> None:
    calls: list[str] = []
    purge, _, clock, _ = _build_purge(segment_count=0, side_channel_counts=[0, 0], calls=calls)

    await purge.purge(new_cell_id(clock), new_node_id(clock), actor="system")

    assert calls == ["segment", "side_0", "side_1"]


async def test_purge_records_one_cell_purged_event_with_counts_only() -> None:
    calls: list[str] = []
    purge, trail, clock, queen_node = _build_purge(
        segment_count=4, side_channel_counts=[1], calls=calls
    )
    cell_id = new_cell_id(clock)
    segment_node = new_node_id(clock)

    await purge.purge(cell_id, segment_node, actor="human")

    recorded = await trail.query(TrailQuery())
    assert len(recorded) == 1
    event = recorded[0]
    assert event.kind == "cell.purged"
    assert event.subject_id == cell_id
    assert event.node_id == queen_node
    assert event.actor == "human"
    assert event.payload == {
        "segment_node_id": segment_node,
        "events_purged": 4,
        "side_channel_records_purged": 1,
    }
