"""Tests for hivemind.wardens.trail_sync: WaggleTrailSync ships a node's own trail segment.

Fits into the Hive:
    Mirrors src/hivemind/wardens/trail_sync.py (codingrules section 3). Drives a real
    `MemoryTransport` pair (roadmap step 5.3's own sender side); `hivemind.queen.trail.sync`'s
    own tests reassemble the chunks this module sends.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.trail_sync for the module under test.
    - hivemind.queen.trail.sync for TrailSegmentReceiver, the chunks' own reassembler.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens.trail_sync import SEGMENT_FORMAT_VERSION, TrailSyncDeps, WaggleTrailSync
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.ids import (
    CellId,
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
class _Scenario:
    """Everything one test needs: the sender, its own trail, its ids and the receiving peer."""

    sync: WaggleTrailSync
    trail: MemoryPheromoneTrail
    receiver: MemoryTransport
    node_id: NodeId
    cell_id: CellId
    warden_id: WardenId


def _event(clock: FakeClock, node_id: NodeId) -> CellEvent:
    """A well-formed CellEvent recorded on `node_id`."""
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


def _build(clock: FakeClock) -> _Scenario:
    """Build a WaggleTrailSync over a fresh trail and the receiving end of its transport."""
    trail = MemoryPheromoneTrail(clock)
    node_id, cell_id, warden_id = new_node_id(clock), new_cell_id(clock), new_warden_id(clock)
    sender_transport, receiver_transport = MemoryTransport.pair(Codec(), Codec())
    sync = WaggleTrailSync(
        TrailSyncDeps(
            trail=trail,
            transport=sender_transport,
            node_id=node_id,
            cell_id=cell_id,
            warden_id=warden_id,
            hive_id=new_hive_id(clock),
            clock=clock,
        )
    )
    return _Scenario(
        sync=sync,
        trail=trail,
        receiver=receiver_transport,
        node_id=node_id,
        cell_id=cell_id,
        warden_id=warden_id,
    )


async def _assert_nothing_sent(receiver: MemoryTransport) -> None:
    """Close `receiver`'s own peer end and assert nothing was ever sent to it."""
    await receiver.close()
    events = [envelope async for envelope in receiver.receive()]
    assert events == []


async def test_sync_sends_nothing_for_an_empty_segment() -> None:
    scenario = _build(FakeClock())

    await scenario.sync.sync()

    await _assert_nothing_sent(scenario.receiver)


async def test_sync_sends_one_final_chunk_carrying_every_event() -> None:
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))

    await scenario.sync.sync()

    envelope = await anext(scenario.receiver.receive())
    assert isinstance(envelope.payload, TrailSegmentSync)
    message = envelope.payload
    assert message.node_id == scenario.node_id
    assert message.cell_id == scenario.cell_id
    assert message.warden_id == scenario.warden_id
    assert message.event_count == 1
    assert message.final is True
    assert message.segment_format_version == SEGMENT_FORMAT_VERSION
    assert message.sha256 == hashlib.sha256(message.chunk).hexdigest()


async def test_sync_is_a_no_op_when_the_segment_has_not_grown() -> None:
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))
    await scenario.sync.sync()
    await anext(scenario.receiver.receive())  # Drain the first sync's own chunk.

    await scenario.sync.sync()  # Nothing new recorded since: must send nothing more.

    await _assert_nothing_sent(scenario.receiver)


async def test_sync_reports_false_without_raising_when_the_link_has_already_closed() -> None:
    """Phase-7 handoff open item 8: a closed peer must never raise out of `sync()`."""
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))
    # The peer's own clean close is final for this end's own send (waggle.transport.memory's
    # own contract), the same way a Warden's real Queen link can close out from under it.
    await scenario.receiver.close()

    sent = await scenario.sync.sync()  # Must not raise.

    assert sent is False


async def test_sync_ships_a_second_export_once_more_events_are_recorded() -> None:
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))
    await scenario.sync.sync()
    await anext(scenario.receiver.receive())  # Drain the first sync's own chunk.

    await scenario.trail.record(_event(clock, scenario.node_id))
    await scenario.sync.sync()

    second = await anext(scenario.receiver.receive())
    assert isinstance(second.payload, TrailSegmentSync)
    assert second.payload.event_count == 2  # export_segment(since=...) is inclusive of the first.
