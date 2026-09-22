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

from hivemind.pheromone.events import (
    CellEvent,
    LlmEvent,
    PheromoneEvent,
    QueenEvent,
    TaskEvent,
    WardenEvent,
)
from hivemind.pheromone.retention import (
    MemorySegmentPurge,
    NightVeilTeardownPurge,
    PurgeReport,
    TrailRecorder,
)
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import CellId, NodeId, new_cell_id, new_event_id, new_hive_id, new_node_id


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


# ──────────────────────────────────────────────────────────────────────────────
# codingrules section 12: a Night Veil trail keeps exactly the lifecycle skeleton, nothing else.
#
# Unlike the fake-based tests above (which check NightVeilTeardownPurge's own orchestration), this
# section runs the real MemorySegmentPurge over a real MemoryPheromoneTrail: it plants a realistic
# mix of events on the Cell's own Warden segment (the kind of thing session commands, episode
# records and llm.call events would produce -- none of it is meant to survive) and on the Queen's
# own segment (the codingrules-12 skeleton itself, which purge_segment never touches because it
# only ever deletes rows for the one node_id it is given), then asserts the whole trail holds
# exactly the skeleton plus the one cell.purged event the purge itself adds.
# ──────────────────────────────────────────────────────────────────────────────


def _event(
    cls: type[PheromoneEvent], *, node_id: NodeId, hive_id: object, clock: FakeClock, kind: str
) -> PheromoneEvent:
    """Build one minimal, valid event of `cls`, stamped on `node_id`'s own segment."""
    return cls(
        id=new_event_id(clock),
        hive_id=hive_id,
        node_id=node_id,
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id="cell_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        payload={},
    )


async def test_a_night_veil_trail_keeps_exactly_the_skeleton_kinds_after_purge() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    hive_id = new_hive_id(clock)
    queen_node = new_node_id(clock)
    warden_node = new_node_id(clock)  # The Night Veil Cell's own Warden: this segment is purged.
    cell_id = new_cell_id(clock)

    # The codingrules-12 skeleton itself, recorded on the Queen's own segment -- exactly the list
    # ADR-0030 names, one event per kind (task state transitions get one representative kind here;
    # the boundary is which node_id recorded the event, not which specific task kind it is).
    skeleton_kinds: list[tuple[type[PheromoneEvent], str]] = [
        (CellEvent, "cell.provisioned"),
        (CellEvent, "cell.attested"),
        (QueenEvent, "queen.placed"),
        (TaskEvent, "task.started"),
        (CellEvent, "cell.sting_cut"),
        (CellEvent, "cell.destroyed"),
    ]
    for cls, kind in skeleton_kinds:
        await trail.record(_event(cls, node_id=queen_node, hive_id=hive_id, clock=clock, kind=kind))

    # Everything a Night Veil Cell's own Warden would have written -- session/episode-adjacent
    # activity and llm.call-family events -- landing on ITS OWN segment, which purge removes
    # wholesale regardless of kind.
    warden_segment_kinds: list[tuple[type[PheromoneEvent], str]] = [
        (WardenEvent, "warden.started"),
        (WardenEvent, "warden.active"),
        (LlmEvent, "llm.rebound"),
        (LlmEvent, "llm.spill"),
        (TaskEvent, "task.progressed"),  # A progress update, not the skeleton's task.started.
    ]
    for cls, kind in warden_segment_kinds:
        await trail.record(
            _event(cls, node_id=warden_node, hive_id=hive_id, clock=clock, kind=kind)
        )

    purge = NightVeilTeardownPurge(
        segments=MemorySegmentPurge(trail),
        side_channels=(),
        recorder=TrailRecorder(trail=trail, clock=clock, hive_id=hive_id, node_id=queen_node),
    )
    await purge.purge(cell_id, warden_node, actor="system")

    recorded = await trail.query(TrailQuery())
    kinds = {event.kind for event in recorded}
    # Exactly the skeleton, plus the one event the purge itself adds -- nothing from the Warden's
    # own segment survives, whatever kind it was.
    assert kinds == {kind for _, kind in skeleton_kinds} | {"cell.purged"}
    assert all(event.node_id == queen_node for event in recorded)
