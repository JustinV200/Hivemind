"""Tests for the receiver's authenticity check: a signed export merges, a forged one does not.

A receiver built with a `SegmentKeys` checks the final chunk's signature before it merges anything
(roadmap 11.9, ADR-0053): a Swarm node's segment must be signed by the key the registry holds, a
Virtual Cell's is verified when present. Each case drives a real export through a real receiver over
a real in-memory trail: a good signature merges, an unsigned segment of a node that must sign, a
forged one and one the Queen holds no key for are refused with their typed error and put nothing on
the trail, a refused export leaves no partial buffer behind to poison the sender's retry, and a
receiver built without keys behaves exactly as it always did.

Fits into the Hive:
    Mirrors src/hivemind/queen/trail/sync.py (codingrules section 3); split by feature (14.2) from
    test_sync.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.queen.trail.test_signature for the check itself.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest
from builders.segments import SegmentRig, make_segment_rig, segment_chunks, signed

from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.trail import (
    BadSegmentSignatureError,
    SegmentKey,
    TrailSegmentReceiver,
    UnsignedSegmentError,
)
from waggle.messages.swarm import TrailSegmentSync
from waggle.signing import Ed25519Signer


class _Keys:
    """A SegmentKeys that answers with one fixed key, and remembers whom it was asked about."""

    def __init__(self, key: SegmentKey | None) -> None:
        self._key = key
        self.asked: list[str] = []

    async def key_for(self, message: TrailSegmentSync) -> SegmentKey | None:
        self.asked.append(message.node_id)
        return self._key


class _Setup(NamedTuple):
    """A receiver, the Queen trail it merges into, and the keys its gate answers with."""

    receiver: TrailSegmentReceiver
    trail: MemoryPheromoneTrail
    keys: _Keys


def _setup(rig: SegmentRig, key: SegmentKey | None) -> _Setup:
    """A receiver over a fresh Queen trail whose gate answers `key` for every node."""
    keys = _Keys(key)
    trail = MemoryPheromoneTrail(rig.clock)
    return _Setup(TrailSegmentReceiver(trail, keys=keys), trail, keys)


def _registered(rig: SegmentRig, *, required: bool = True) -> SegmentKey:
    """The key the rig's node is registered with."""
    return SegmentKey(public_key=rig.signer.public_key_bytes, is_required=required)


async def _merged(trail: MemoryPheromoneTrail) -> int:
    """How many exported events reached the Queen's trail."""
    return len(await trail.query(TrailQuery(kind="cell.provisioned")))


async def _feed(receiver: TrailSegmentReceiver, chunks: list[TrailSegmentSync]) -> list[int]:
    """Hand every chunk to `receiver` in order; the merged counts it returned (0 until final)."""
    return [await receiver.receive(chunk) for chunk in chunks]


async def test_a_segment_signed_by_its_nodes_key_merges() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, _registered(rig))

    results = await _feed(setup.receiver, signed(await segment_chunks(rig), rig.signer))

    assert results == [1]
    assert await _merged(setup.trail) == 1
    assert setup.keys.asked == [rig.node_id]  # Asked once, on the final chunk.


async def test_the_key_is_asked_for_only_on_the_final_chunk_of_a_multi_chunk_export() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, _registered(rig))
    chunks = signed(await segment_chunks(rig, events=3, chunk_bytes=64), rig.signer)
    assert len(chunks) > 2

    results = await _feed(setup.receiver, chunks)

    assert results[-1] == 3
    assert setup.keys.asked == [rig.node_id]


async def test_an_unsigned_segment_of_a_node_that_must_sign_is_refused_and_merges_nothing() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, _registered(rig))

    with pytest.raises(UnsignedSegmentError):
        await _feed(setup.receiver, await segment_chunks(rig))

    assert await _merged(setup.trail) == 0


async def test_a_forged_segment_is_refused_and_merges_nothing() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, _registered(rig))
    forged = signed(await segment_chunks(rig), Ed25519Signer.generate())

    with pytest.raises(BadSegmentSignatureError):
        await _feed(setup.receiver, forged)

    assert await _merged(setup.trail) == 0


async def test_a_segment_of_a_node_the_queen_holds_no_key_for_is_refused() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, SegmentKey(public_key=None, is_required=True))

    with pytest.raises(BadSegmentSignatureError):
        await _feed(setup.receiver, signed(await segment_chunks(rig), rig.signer))

    assert await _merged(setup.trail) == 0


async def test_a_refused_export_leaves_no_partial_buffer_for_the_senders_retry() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, _registered(rig))
    chunks = await segment_chunks(rig, events=3, chunk_bytes=64)
    with pytest.raises(UnsignedSegmentError):
        await _feed(setup.receiver, chunks)

    # The same export again, now signed: reassembled from nothing, it merges whole.
    results = await _feed(setup.receiver, signed(chunks, rig.signer))

    assert results[-1] == 3
    assert await _merged(setup.trail) == 3


async def test_an_optional_key_lets_an_unsigned_segment_through_but_not_a_forged_one() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, _registered(rig, required=False))

    assert await _feed(setup.receiver, await segment_chunks(rig)) == [1]
    forged = signed(await segment_chunks(make_segment_rig()), Ed25519Signer.generate())
    with pytest.raises(BadSegmentSignatureError):
        await _feed(setup.receiver, forged)


async def test_a_receiver_without_keys_merges_a_segment_whether_signed_or_not() -> None:
    rig = make_segment_rig()
    receiver = TrailSegmentReceiver(MemoryPheromoneTrail(rig.clock))

    assert await _feed(receiver, await segment_chunks(rig)) == [1]
    signed_by_a_stranger = signed(
        await segment_chunks(make_segment_rig()), Ed25519Signer.generate()
    )
    assert await _feed(receiver, signed_by_a_stranger) == [1]


async def test_a_gate_with_no_opinion_checks_nothing() -> None:
    rig = make_segment_rig()
    setup = _setup(rig, None)

    assert await _feed(setup.receiver, await segment_chunks(rig)) == [1]
    assert setup.keys.asked == [rig.node_id]  # Asked, and it had nothing to say.
