"""Tests for hivemind.queen.trail.sync: TrailSegmentReceiver reassembles and merges chunks.

The Queen-side half of `hivemind.wardens.trail_sync` (roadmap step 5.3 / ADR-0027): this module
drives a real `hivemind.wardens.trail_sync.WaggleTrailSync` sender over a `MemoryTransport` pair,
reads whatever `TrailSegmentSync` chunks it actually produces off the wire, and feeds them to
`TrailSegmentReceiver` -- the same shape the eventual listener wiring will use (this dispatch's own
report names that gap), just without a real transport loop around it.

Fits into the Hive:
    Mirrors src/hivemind/queen/trail/sync.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.trail.sync for the module under test.
    - hivemind.wardens.trail_sync for WaggleTrailSync, the sender these chunks come from.
"""

from __future__ import annotations

import pytest

from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.trail.sync import (
    CorruptSegmentError,
    TrailSegmentReceiver,
    UnknownSegmentFormatError,
)
from hivemind.wardens import trail_sync as wardens_trail_sync
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.ids import NodeId, new_cell_id, new_event_id, new_hive_id, new_node_id, new_warden_id
from waggle.messages.swarm import TrailSegmentSync
from waggle.transport.memory import MemoryTransport


def _event(clock: FakeClock, node_id: NodeId, payload_size: int = 0) -> CellEvent:
    """A well-formed CellEvent recorded on `node_id`, optionally padded to force a bigger export."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=node_id,
        at=clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=new_cell_id(clock),
        payload={"pad": "x" * payload_size} if payload_size else {},
    )


async def _sent_chunks(
    clock: FakeClock, node_id: NodeId, trail: MemoryPheromoneTrail
) -> list[TrailSegmentSync]:
    """Sync `trail`'s own segment over a real WaggleTrailSync and collect every chunk it sent."""
    sender_transport, receiver_transport = MemoryTransport.pair(Codec(), Codec())
    sync = WaggleTrailSync(
        TrailSyncDeps(
            trail=trail,
            transport=sender_transport,
            node_id=node_id,
            cell_id=new_cell_id(clock),
            warden_id=new_warden_id(clock),
            hive_id=new_hive_id(clock),
            clock=clock,
        )
    )
    await sync.sync()
    await receiver_transport.close()
    return [
        envelope.payload
        async for envelope in receiver_transport.receive()
        if isinstance(envelope.payload, TrailSegmentSync)
    ]


async def test_receive_merges_a_single_chunk_export_into_the_queens_trail() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_event(clock, node_id))
    chunks = await _sent_chunks(clock, node_id, source_trail)
    assert len(chunks) == 1  # One small event fits in one chunk (MAX_CHUNK_BYTES).

    queen_trail = MemoryPheromoneTrail(clock)
    receiver = TrailSegmentReceiver(queen_trail)
    inserted = await receiver.receive(chunks[0])

    assert inserted == 1


async def test_receive_reassembles_several_chunks_before_merging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A tiny chunk size forces WaggleTrailSync to split one export into several frames, the same
    # shape a large real segment produces at the real MAX_CHUNK_BYTES.
    monkeypatch.setattr(wardens_trail_sync, "MAX_CHUNK_BYTES", 64)
    clock = FakeClock()
    node_id = new_node_id(clock)
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_event(clock, node_id, payload_size=500))
    chunks = await _sent_chunks(clock, node_id, source_trail)
    assert len(chunks) > 1

    queen_trail = MemoryPheromoneTrail(clock)
    receiver = TrailSegmentReceiver(queen_trail)
    results = [await receiver.receive(chunk) for chunk in chunks]

    assert results[:-1] == [0] * (len(chunks) - 1)  # Nothing merged before the final chunk.
    assert results[-1] == 1


async def test_receive_is_idempotent_by_event_id() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_event(clock, node_id))
    chunks = await _sent_chunks(clock, node_id, source_trail)

    queen_trail = MemoryPheromoneTrail(clock)
    receiver = TrailSegmentReceiver(queen_trail)
    first = await receiver.receive(chunks[0])
    second = await receiver.receive(chunks[0])

    assert first == 1
    assert second == 0


async def test_receive_refuses_an_unknown_segment_format_version() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_event(clock, node_id))
    chunks = await _sent_chunks(clock, node_id, source_trail)
    bad_version = chunks[0].model_copy(update={"segment_format_version": 999})

    receiver = TrailSegmentReceiver(MemoryPheromoneTrail(clock))
    with pytest.raises(UnknownSegmentFormatError):
        await receiver.receive(bad_version)


async def test_receive_refuses_a_reassembled_export_with_a_mismatched_digest() -> None:
    clock = FakeClock()
    node_id = new_node_id(clock)
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_event(clock, node_id))
    chunks = await _sent_chunks(clock, node_id, source_trail)
    corrupted = chunks[0].model_copy(update={"sha256": "0" * 64})

    receiver = TrailSegmentReceiver(MemoryPheromoneTrail(clock))
    with pytest.raises(CorruptSegmentError):
        await receiver.receive(corrupted)
