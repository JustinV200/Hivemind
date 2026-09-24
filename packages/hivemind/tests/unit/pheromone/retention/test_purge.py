"""Tests for hivemind.pheromone.retention.purge: NightVeilTeardownPurge and its side channels.

hivemind.pheromone.retention.SegmentPurge itself (SqliteSegmentPurge, MemorySegmentPurge) has its
own contract suite in tests/contracts/test_pheromone_trail_contract.py; this module covers the
purge's own orchestration -- the order of its five steps, what it counts, the one Capping summary
per tier it keeps, the Queen's own node it never touches, and the counts-only `cell.purged` it
records last -- over a real MemoryPheromoneTrail and EphemeralSegments, with fake purgers standing
in where only the order matters.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/retention/purge.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention.purge for the module under test.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import JsonValue

from hivemind.pheromone.events import CappingEvent, CellEvent, PheromoneEvent, WardenEvent
from hivemind.pheromone.retention import (
    EphemeralSegments,
    MemorySegmentPurge,
    NightVeilTeardownPurge,
    PurgeReport,
    SideChannels,
    TrailRecorder,
)
from hivemind.pheromone.retention import purge as purge_module
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery, TrailSegment
from waggle.clock import FakeClock
from waggle.ids import (
    CellId,
    HiveId,
    NodeId,
    new_cell_id,
    new_event_id,
    new_hive_id,
    new_node_id,
    new_task_id,
    new_warden_id,
)


class _FakeSegmentPurge:
    """A SegmentPurge that records each node it is asked to purge and removes nothing."""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.nodes: list[NodeId] = []

    async def purge_segment(self, node_id: NodeId) -> int:
        self._calls.append("segment")
        self.nodes.append(node_id)
        return 0


class _FakeSideChannel:
    """A SideChannelPurger that records that it ran, and the members it was handed."""

    def __init__(self, count: int, calls: list[str], name: str) -> None:
        self._count, self._calls, self._name = count, calls, name
        self.members: list[frozenset[str]] = []

    async def purge(self, cell_id: CellId, members: frozenset[str]) -> int:
        self._calls.append(self._name)
        self.members.append(members)
        return self._count


class _HangingSideChannel:
    """A SideChannelPurger whose store never answers."""

    async def purge(self, cell_id: CellId, members: frozenset[str]) -> int:
        await asyncio.Event().wait()
        return 0


class _FakeMembers:
    """A MemberSource that ties fixed ids to every Cell it is asked about."""

    def __init__(self, *ids: str) -> None:
        self._ids = frozenset(ids)

    async def members_of(self, cell_id: CellId) -> frozenset[str]:
        return self._ids


class _Hive:
    """One Queen's durable trail, recorder identity and ephemeral segments, for one test."""

    def __init__(self) -> None:
        self.clock = FakeClock()
        self.trail = MemoryPheromoneTrail(self.clock)
        self.hive_id: HiveId = new_hive_id(self.clock)
        self.queen_node: NodeId = new_node_id(self.clock)
        self.recorder = TrailRecorder(
            trail=self.trail, clock=self.clock, hive_id=self.hive_id, node_id=self.queen_node
        )
        self.segments = EphemeralSegments(self.clock)

    def purge(self, side_channels: SideChannels | None = None) -> NightVeilTeardownPurge:
        """A purge over the real durable trail and segments, with these side channels."""
        return NightVeilTeardownPurge(
            MemorySegmentPurge(self.trail),
            side_channels if side_channels is not None else SideChannels(),
            self.recorder,
            ephemeral=self.segments,
        )

    def event(
        self,
        cls: type[PheromoneEvent],
        kind: str,
        subject_id: str,
        node_id: NodeId | None = None,
        payload: dict[str, JsonValue] | None = None,
    ) -> PheromoneEvent:
        """One valid event, on the Queen's node unless `node_id` names another."""
        return cls(
            id=new_event_id(self.clock),
            hive_id=self.hive_id,
            node_id=node_id if node_id is not None else self.queen_node,
            at=self.clock.now(),
            actor="system",
            kind=kind,
            subject_id=subject_id,
            payload=payload or {},
        )


def _capping(hive: _Hive, node: NodeId, proposal: str, kind: str, tier: str | None) -> CappingEvent:
    payload: dict[str, JsonValue] = {"tier": tier} if tier is not None else {}
    event = hive.event(CappingEvent, kind, proposal, node, payload)
    assert isinstance(event, CappingEvent)
    return event


async def _ship(hive: _Hive, cell_id: CellId, node: NodeId, *events: PheromoneEvent) -> None:
    """Ship `events` as the Cell's own Warden segment into its held ephemeral segment."""
    segment = TrailSegment(node_id=node, exported_at=hive.clock.now(), events=events)
    assert await hive.segments.merge(cell_id, segment) == len(events)


async def test_the_purge_takes_the_segment_and_records_counts_only() -> None:
    hive = _Hive()
    cell_id, warden_node = new_cell_id(hive.clock), new_node_id(hive.clock)
    hive.segments.open(cell_id)
    warden = "warden_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    await _ship(
        hive, cell_id, warden_node, hive.event(WardenEvent, "warden.started", warden, warden_node)
    )

    report = await hive.purge().purge(cell_id, actor="human")

    assert report == PurgeReport(
        cell_id=cell_id, events_purged=1, side_channel_records_purged=0, summaries=0
    )
    [purged] = await hive.trail.query(TrailQuery(kind="cell.purged"))
    assert purged.subject_id == cell_id and purged.actor == "human"
    assert purged.node_id == hive.queen_node
    assert purged.payload == {"events_purged": 1, "side_channel_records_purged": 0}
    assert not hive.segments.holds(cell_id)


async def test_the_purge_keeps_one_capping_summary_per_tier_with_counts() -> None:
    hive = _Hive()
    cell_id, node = new_cell_id(hive.clock), new_node_id(hive.clock)
    hive.segments.open(cell_id)
    detail = [
        _capping(hive, node, "msg_01HAAAAAAAAAAAAAAAAAAAAAAA", "capping.proposed", "SCRATCH_WRITE"),
        _capping(hive, node, "msg_01HAAAAAAAAAAAAAAAAAAAAAAA", "capping.capped", "SCRATCH_WRITE"),
        _capping(hive, node, "msg_01HBBBBBBBBBBBBBBBBBBBBBBB", "capping.proposed", "SCRATCH_WRITE"),
        _capping(hive, node, "msg_01HBBBBBBBBBBBBBBBBBBBBBBB", "capping.rejected", "SCRATCH_WRITE"),
        _capping(
            hive, node, "msg_01HCCCCCCCCCCCCCCCCCCCCCCC", "capping.proposed", "NETWORK_EGRESS"
        ),
        _capping(hive, node, "msg_01HCCCCCCCCCCCCCCCCCCCCCCC", "capping.capped", "NETWORK_EGRESS"),
        _capping(hive, node, "msg_01HCCCCCCCCCCCCCCCCCCCCCCC", "capping.rolled_back", None),
    ]
    await _ship(hive, cell_id, node, *detail)

    report = await hive.purge().purge(cell_id, actor="system")

    summaries = await hive.trail.query(TrailQuery(kind="capping.summary"))
    assert [event.payload for event in summaries] == [
        {"tier": "NETWORK_EGRESS", "approved": 1, "rejected": 0, "rolled_back": 1},
        {"tier": "SCRATCH_WRITE", "approved": 1, "rejected": 1, "rolled_back": 0},
    ]
    assert all(event.subject_id == cell_id for event in summaries)
    assert report.summaries == 2 and report.events_purged == len(detail)
    # Nothing but the summaries and the purge's own record reached the durable trail.
    kinds = {event.kind for event in await hive.trail.query(TrailQuery())}
    assert kinds == {"capping.summary", "cell.purged"}


async def test_the_purge_runs_summaries_segment_side_channels_then_its_own_record() -> None:
    hive, calls = _Hive(), list[str]()
    cell_id, node = new_cell_id(hive.clock), new_node_id(hive.clock)
    segments = _FakeSegmentPurge(calls)
    sides = SideChannels(nectar=_FakeSideChannel(2, calls, "nectar"))
    purge = NightVeilTeardownPurge(segments, sides, hive.recorder, ephemeral=hive.segments)

    report = await purge.purge(cell_id, actor="system", segment_node_ids=(node,))

    assert calls == ["segment", "nectar"]
    assert segments.nodes == [node]
    assert report.side_channel_records_purged == 2
    [last] = await hive.trail.query(TrailQuery(newest_first=True, limit=1))
    assert last.kind == "cell.purged"


async def test_the_purge_removes_the_cells_durable_rows_but_never_the_queens_own() -> None:
    hive = _Hive()
    cell_id, leaked_node = new_cell_id(hive.clock), new_node_id(hive.clock)
    # A segment merged before the Cell was known to be Night Veil: its rows sit on the trail.
    leaked = hive.event(CellEvent, "cell.leased", cell_id, leaked_node)
    queens = hive.event(CellEvent, "cell.provisioned", cell_id)
    await hive.trail.record(leaked)
    await hive.trail.record(queens)

    # Even named outright, the Queen's own node is never purged: it would take her whole trail.
    report = await hive.purge().purge(cell_id, actor="system", segment_node_ids=(hive.queen_node,))

    remaining = await hive.trail.query(TrailQuery())
    assert [event.id for event in remaining if event.kind != "cell.purged"] == [queens.id]
    assert report.events_purged == 1


async def test_side_channels_run_in_their_registered_order_and_seams_are_skipped() -> None:
    calls = list[str]()
    tor, honey, memory, chamber, images, ledger = (
        _FakeSideChannel(0, calls, name)
        for name in ("tor", "honey", "memory", "chamber", "images", "ledger")
    )

    registered = SideChannels(
        forage_ledger=ledger,
        snapshot_images=images,
        brood_chamber=chamber,
        memory=memory,
        honey=honey,
        tor_hidden_service=tor,
    ).registered()

    assert registered == (tor, honey, memory, chamber, images, ledger)
    assert SideChannels().registered() == ()


async def test_every_side_channel_is_handed_the_cells_filed_ids_and_its_sources_ids() -> None:
    hive, calls = _Hive(), list[str]()
    cell_id = new_cell_id(hive.clock)
    task_id, warden_id = new_task_id(hive.clock), new_warden_id(hive.clock)
    hive.segments.open(cell_id)
    hive.segments.bind(task_id, cell_id)
    hive.segments.file(warden_id, cell_id)
    memory, ledger = _FakeSideChannel(1, calls, "memory"), _FakeSideChannel(2, calls, "ledger")
    # A store that still ties an older task to the Cell (and names the Cell itself, too).
    older = new_task_id(hive.clock)
    sides = SideChannels(
        memory=memory, forage_ledger=ledger, members=(_FakeMembers(older, cell_id),)
    )

    report = await hive.purge(sides).purge(cell_id, actor="system")

    expected = frozenset({task_id, warden_id, older})
    assert memory.members == ledger.members == [expected]
    assert report.side_channel_records_purged == 3


async def test_attach_replaces_the_side_channels_every_later_purge_runs() -> None:
    hive, calls = _Hive(), list[str]()
    purge = hive.purge(SideChannels(nectar=_FakeSideChannel(5, calls, "nectar")))
    purge.attach(SideChannels(memory=_FakeSideChannel(1, calls, "memory")))

    report = await purge.purge(new_cell_id(hive.clock), actor="system")

    assert calls == ["memory"]
    assert report.side_channel_records_purged == 1


async def test_a_side_channel_that_never_answers_fails_the_purge_before_its_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hive = _Hive()
    cell_id = new_cell_id(hive.clock)
    hive.segments.open(cell_id)
    monkeypatch.setattr(purge_module, "SIDE_CHANNEL_TIMEOUT_S", 0.01)

    with pytest.raises(TimeoutError):
        await hive.purge(SideChannels(nectar=_HangingSideChannel())).purge(cell_id, actor="system")

    # The segment is gone all the same; only the claim that the purge finished is missing.
    assert not hive.segments.holds(cell_id)
    assert await hive.trail.query(TrailQuery(kind="cell.purged")) == ()


async def test_a_purge_with_no_ephemeral_store_still_purges_and_records() -> None:
    hive = _Hive()
    cell_id = new_cell_id(hive.clock)
    purge = NightVeilTeardownPurge(MemorySegmentPurge(hive.trail), SideChannels(), hive.recorder)

    report = await purge.purge(cell_id, actor="system")

    assert report.events_purged == 0
    assert len(await hive.trail.query(TrailQuery(kind="cell.purged"))) == 1
