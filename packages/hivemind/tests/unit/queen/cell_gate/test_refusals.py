"""Tests for hivemind.queen.cell_gate.refusals: what a Cell's link refused, on the Queen's trail.

Each test gives one link's `LinkRefusals` real chunks, cut by a real `WaggleTrailSync` from a real
segment, in envelopes wrapped as the link's own node and Warden, and a real receiver over the
Queen's trail. A chunk that names another node, or carries another node's segment, is refused as
`another_node`; one that names another Cell as `another_cell`, which is what keeps a MEADOW Cell's
segment out of a Night Veil Cell's ephemeral segment and a Night Veil Cell's off the durable trail
(the receiver routes by the chunk's Cell); an unknown format and a digest that does not match are
refused as `format` and `corrupt`; nothing of a refused chunk is merged. Each reason is recorded
once per link, a signature failure is recorded by its code's last word, and a proved chunk merges
and records nothing.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/refusals.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.queen.cell_gate.test_listener for the same refusals over a real socket.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.pheromone import (
    CellEvent,
    EphemeralSegments,
    PheromoneEvent,
    TrailQuery,
    TrailRecorder,
    TrailSegment,
    VeiledTrail,
)
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate import (
    ENVELOPE_REFUSED_KIND,
    SEGMENT_REFUSED_KIND,
    LinkRefusals,
    SegmentRefusal,
)
from hivemind.queen.trail import TrailSegmentReceiver
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import InvalidSignatureError
from waggle.ids import (
    CellId,
    HiveId,
    NodeId,
    WardenId,
    new_cell_id,
    new_event_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.swarm import TrailSegmentSync
from waggle.transport.memory import MemoryTransport


@dataclass(frozen=True, slots=True)
class _Link:
    """One Cell's proved link: who it is, the Queen's trail, and what records its refusals."""

    clock: FakeClock
    hive_id: HiveId
    cell_id: CellId
    node_id: NodeId
    warden_id: WardenId
    queen_trail: MemoryPheromoneTrail
    receiver: TrailSegmentReceiver
    refusals: LinkRefusals

    def envelope(self, chunk: TrailSegmentSync) -> Envelope:
        """`chunk` in an envelope from this link's own Warden and node, as its codec proved."""
        hop = Hop(sender=self.warden_id, recipient=self.hive_id, node_id=self.node_id)
        return wrap(chunk, hop, clock=self.clock)

    async def refused(self) -> list[PheromoneEvent]:
        """Every refusal recorded on the Queen's trail, oldest first."""
        kinds = (ENVELOPE_REFUSED_KIND, SEGMENT_REFUSED_KIND)
        return [e for e in await self.queen_trail.query(TrailQuery()) if e.kind in kinds]


def _link(recorded: bool = True, segments: EphemeralSegments | None = None) -> _Link:
    """A fresh link and the Queen's trail it merges into; `recorded=False` has no recorder.

    With `segments`, the Queen records and receives through the Night Veil boundary over them, as
    a Hive with a Virtual side does; `queen_trail` stays the durable trail behind it.
    """
    clock = FakeClock()
    hive_id, cell_id, node_id = new_hive_id(clock), new_cell_id(clock), new_node_id(clock)
    queen_trail = MemoryPheromoneTrail(clock)
    trail = queen_trail if segments is None else VeiledTrail(queen_trail, segments)
    recorder = TrailRecorder(trail=trail, clock=clock, hive_id=hive_id, node_id=new_node_id(clock))
    refusals = LinkRefusals(recorder if recorded else None, cell_id, node_id)
    receiver = TrailSegmentReceiver(trail, segments)
    warden_id = new_warden_id(clock)
    return _Link(clock, hive_id, cell_id, node_id, warden_id, queen_trail, receiver, refusals)


async def _chunk(link: _Link, node_id: NodeId | None = None) -> TrailSegmentSync:
    """One chunk of a real export of one event recorded on `node_id` (the link's own by default)."""
    node = node_id if node_id is not None else link.node_id
    source = MemoryPheromoneTrail(link.clock)
    await source.record(_provisioned(link, node))
    sender, receiver = MemoryTransport.pair(Codec(), Codec())
    deps = TrailSyncDeps(
        trail=source,
        transport=sender,
        node_id=node,
        cell_id=link.cell_id,
        warden_id=link.warden_id,
        hive_id=link.hive_id,
        clock=link.clock,
    )
    await WaggleTrailSync(deps).sync()
    await receiver.close()
    [chunk] = [
        e.payload async for e in receiver.receive() if isinstance(e.payload, TrailSegmentSync)
    ]
    return chunk


def _provisioned(link: _Link, node_id: NodeId) -> CellEvent:
    """A `cell.provisioned` recorded on `node_id`: what a merged segment puts on the trail."""
    return CellEvent(
        id=new_event_id(link.clock),
        hive_id=link.hive_id,
        node_id=node_id,
        at=link.clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=link.cell_id,
        payload={},
    )


async def _merged(link: _Link) -> int:
    """How many `cell.provisioned` events reached the Queen's trail."""
    return len(await link.queen_trail.query(TrailQuery(kind="cell.provisioned")))


async def _reasons(link: _Link) -> list[tuple[str, object]]:
    """Each recorded refusal's kind and reason, oldest first."""
    return [(event.kind, event.payload["reason"]) for event in await link.refused()]


async def test_a_proved_chunk_merges_and_nothing_is_recorded() -> None:
    link = _link()
    chunk = await _chunk(link)

    await link.refusals.merge(link.receiver, link.envelope(chunk), chunk)

    assert await _merged(link) == 1
    assert await link.refused() == []


async def test_a_chunk_naming_another_node_is_refused_before_the_receiver_sees_it() -> None:
    link = _link()
    chunk = (await _chunk(link)).model_copy(update={"node_id": new_node_id(link.clock)})

    await link.refusals.merge(link.receiver, link.envelope(chunk), chunk)

    assert await _merged(link) == 0
    [event] = await link.refused()
    assert (event.kind, event.subject_id) == (SEGMENT_REFUSED_KIND, link.cell_id)
    assert event.payload == {
        "cell_id": link.cell_id,
        "node_id": link.node_id,
        "reason": SegmentRefusal.ANOTHER_NODE.value,
    }


async def test_a_chunk_naming_another_warden_is_refused_too() -> None:
    link = _link()
    chunk = (await _chunk(link)).model_copy(update={"warden_id": new_warden_id(link.clock)})

    await link.refusals.merge(link.receiver, link.envelope(chunk), chunk)

    assert await _merged(link) == 0
    assert await _reasons(link) == [(SEGMENT_REFUSED_KIND, "another_node")]


async def test_a_proved_chunk_carrying_another_nodes_segment_is_refused() -> None:
    link = _link()
    other = new_node_id(link.clock)
    chunk = await _chunk(link, other)  # Cut as `other`'s export, then shipped as the link's own.
    forged = chunk.model_copy(update={"node_id": link.node_id})
    assert TrailSegment.model_validate_json(chunk.chunk).node_id == other

    await link.refusals.merge(link.receiver, link.envelope(forged), forged)

    assert await _merged(link) == 0
    assert await _reasons(link) == [(SEGMENT_REFUSED_KIND, "another_node")]


async def test_a_chunk_naming_another_cell_is_refused_before_the_receiver_sees_it() -> None:
    link = _link()
    chunk = (await _chunk(link)).model_copy(update={"cell_id": new_cell_id(link.clock)})

    await link.refusals.merge(link.receiver, link.envelope(chunk), chunk)

    assert await _merged(link) == 0
    assert await _reasons(link) == [(SEGMENT_REFUSED_KIND, "another_cell")]


async def test_a_meadow_cell_cannot_ship_its_segment_as_a_night_veil_cells() -> None:
    segments = EphemeralSegments(FakeClock())
    link = _link(segments=segments)
    night_veil_cell = new_cell_id(link.clock)
    segments.open(night_veil_cell)
    claimed = (await _chunk(link)).model_copy(update={"cell_id": night_veil_cell})

    await link.refusals.merge(link.receiver, link.envelope(claimed), claimed)

    # Nothing reached the Night Veil Cell's segment, to be purged with it, nor the durable trail.
    assert await segments.query(night_veil_cell, TrailQuery()) == ()
    assert await _merged(link) == 0
    # The MEADOW Cell's own refusal is on the durable trail, for the Guard Bee.
    assert await _reasons(link) == [(SEGMENT_REFUSED_KIND, "another_cell")]


async def test_a_night_veil_cell_cannot_ship_its_segment_onto_the_trail_as_a_meadow_cells() -> None:
    segments = EphemeralSegments(FakeClock())
    link = _link(segments=segments)
    segments.open(link.cell_id)  # The link's own Cell is the Night Veil one.
    claimed = (await _chunk(link)).model_copy(update={"cell_id": new_cell_id(link.clock)})

    await link.refusals.merge(link.receiver, link.envelope(claimed), claimed)

    # Its detail never reached the durable trail; its refusal is veiled with its own record.
    assert await _merged(link) == 0
    assert await link.refused() == []
    held = await segments.query(link.cell_id, TrailQuery())
    assert [(e.kind, e.payload["reason"]) for e in held] == [(SEGMENT_REFUSED_KIND, "another_cell")]


async def test_an_unknown_format_and_a_bad_digest_are_each_recorded_once() -> None:
    link = _link()
    chunk = await _chunk(link)
    unknown = chunk.model_copy(update={"segment_format_version": 999})
    corrupt = chunk.model_copy(update={"sha256": "0" * 64})

    for refused in (unknown, corrupt, unknown, corrupt):  # Each reason twice on one link.
        await link.refusals.merge(link.receiver, link.envelope(refused), refused)

    assert await _merged(link) == 0
    assert await _reasons(link) == [
        (SEGMENT_REFUSED_KIND, "format"),
        (SEGMENT_REFUSED_KIND, "corrupt"),
    ]


async def test_a_signature_failure_is_recorded_by_its_codes_last_word() -> None:
    link = _link()

    await link.refusals.envelope(InvalidSignatureError("A frame failed its signature."))

    [event] = await link.refused()
    assert (event.kind, event.subject_id, event.payload["reason"]) == (
        ENVELOPE_REFUSED_KIND,
        link.cell_id,
        "invalid",
    )
    assert event.node_id != link.node_id  # The Queen's own record, never the Cell's segment.


async def test_without_a_recorder_the_chunk_is_still_refused_and_nothing_recorded() -> None:
    link = _link(recorded=False)
    chunk = (await _chunk(link)).model_copy(update={"node_id": new_node_id(link.clock)})

    await link.refusals.merge(link.receiver, link.envelope(chunk), chunk)
    await link.refusals.envelope(InvalidSignatureError("A frame failed its signature."))

    assert await _merged(link) == 0
    assert await link.refused() == []
