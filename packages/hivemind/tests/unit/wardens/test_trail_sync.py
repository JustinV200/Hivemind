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

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import timedelta

import pytest

from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens.trail_sync import SEGMENT_FORMAT_VERSION, TrailSyncDeps, WaggleTrailSync
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.errors import SignatureError
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
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.swarm import TrailSegmentSync, segment_signing_bytes
from waggle.signing import Ed25519Signer, Ed25519Verifier
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


def _build(clock: FakeClock, signer: Ed25519Signer | None = None) -> _Scenario:
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
            signer=signer,
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


async def test_a_rewound_sync_ships_again_the_stretch_a_dead_link_may_have_swallowed() -> None:
    """A send that returns has only reached a connection; if it was dead the Queen never had it."""
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))
    clock.advance(60.0)
    await scenario.trail.record(_event(clock, scenario.node_id))
    clock.advance(30.0)
    await scenario.trail.record(_event(clock, scenario.node_id))
    await scenario.sync.sync()
    await anext(scenario.receiver.receive())  # The send returned: as far as the sender can tell.
    await scenario.sync.sync()
    await scenario.sync.sync()  # Nothing new, nothing sent: the sender trusts what it sent.

    scenario.sync.rewind(timedelta(seconds=45))
    sent = await scenario.sync.sync()

    again = await _first_chunk(scenario)
    assert sent is True
    # The newest event is 30 s after the middle one and 90 s after the first: 45 s back from the
    # newest takes in the last two, and not the first, which was sent long enough ago to be safe.
    assert again.event_count == 2


async def test_rewinding_before_anything_was_shipped_takes_back_nothing() -> None:
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))

    scenario.sync.rewind(timedelta(seconds=45))
    await scenario.sync.sync()

    first = await _first_chunk(scenario)
    assert first.event_count == 1  # The ordinary first export, whole.


async def test_a_sync_after_a_rewind_is_not_taken_for_a_repeat_of_the_last_one() -> None:
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))
    await scenario.sync.sync()
    await anext(scenario.receiver.receive())

    scenario.sync.rewind(timedelta(seconds=45))
    await scenario.sync.sync()  # The only event is the newest, and the one sent last time.

    again = await _first_chunk(scenario)
    assert again.event_count == 1


# ──────────────────────────────────────────────────────────────────────────────
# Waggle 1.13: the final chunk carries the node's signature over the segment
# ──────────────────────────────────────────────────────────────────────────────


def _signing_input(message: TrailSegmentSync) -> bytes:
    """What the Queen rebuilds from a final chunk alone, and so what must have been signed."""
    assert message.sha256 is not None
    return segment_signing_bytes(
        message.node_id,
        message.first_event_id,
        message.last_event_id,
        message.event_count,
        message.sha256,
    )


async def _first_chunk(scenario: _Scenario) -> TrailSegmentSync:
    """The first frame the scenario's receiver got, narrowed to a trail segment chunk."""
    message = (await anext(scenario.receiver.receive())).payload
    assert isinstance(message, TrailSegmentSync)
    return message


async def test_a_warden_holding_a_signer_signs_the_final_chunk_and_it_verifies() -> None:
    clock = FakeClock()
    signer = Ed25519Signer.generate()
    scenario = _build(clock, signer)
    await scenario.trail.record(_event(clock, scenario.node_id))

    await scenario.sync.sync()

    message = await _first_chunk(scenario)
    assert message.final and message.segment_signature is not None
    Ed25519Verifier({scenario.node_id: signer.public_key_bytes}).verify(
        scenario.node_id, _signing_input(message), message.segment_signature
    )


async def test_a_signature_made_for_another_nodes_key_does_not_verify() -> None:
    clock = FakeClock()
    scenario = _build(clock, Ed25519Signer.generate())
    await scenario.trail.record(_event(clock, scenario.node_id))
    await scenario.sync.sync()
    message = await _first_chunk(scenario)
    assert message.segment_signature is not None
    stranger = Ed25519Verifier({scenario.node_id: Ed25519Signer.generate().public_key_bytes})

    with pytest.raises(SignatureError):
        stranger.verify(scenario.node_id, _signing_input(message), message.segment_signature)


async def test_a_warden_with_no_signer_sends_no_signature() -> None:
    clock = FakeClock()
    scenario = _build(clock)
    await scenario.trail.record(_event(clock, scenario.node_id))

    await scenario.sync.sync()

    assert (await _first_chunk(scenario)).segment_signature is None


async def _record_many(scenario: _Scenario, clock: FakeClock) -> None:
    """Record enough events that one export needs more than one chunk of MAX_CHUNK_BYTES."""
    for _ in range(MAX_CHUNK_BYTES // _EVENT_BYTES_AT_LEAST * 2):
        await scenario.trail.record(_event(clock, scenario.node_id))


_EVENT_BYTES_AT_LEAST = 300  # A recorded event serialises to at least this many bytes.


async def test_only_the_final_chunk_of_a_multi_chunk_export_is_signed() -> None:
    clock = FakeClock()
    scenario = _build(clock, Ed25519Signer.generate())
    await _record_many(scenario, clock)

    await scenario.sync.sync()

    await scenario.receiver.close()
    chunks = [e.payload async for e in scenario.receiver.receive()]
    messages = [c for c in chunks if isinstance(c, TrailSegmentSync)]
    assert len(messages) > 1
    assert [m.segment_signature is not None for m in messages] == [False] * (len(messages) - 1) + [
        True
    ]


async def test_two_syncs_asked_at_once_do_not_interleave_their_chunks() -> None:
    clock = FakeClock()
    scenario = _build(clock, Ed25519Signer.generate())
    await _record_many(scenario, clock)

    await asyncio.gather(scenario.sync.sync(), scenario.sync.sync())

    await scenario.receiver.close()
    chunks = [e.payload async for e in scenario.receiver.receive()]
    offsets = [c.offset for c in chunks if isinstance(c, TrailSegmentSync)]
    # The second caller finds the segment already sent and sends nothing: one export, in order.
    assert offsets == sorted(offsets) and offsets[0] == 0 and len(set(offsets)) == len(offsets)
