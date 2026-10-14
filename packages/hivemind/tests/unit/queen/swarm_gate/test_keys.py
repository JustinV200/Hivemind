"""Tests for hivemind.queen.swarm_gate.keys: the key a Swarm node's trail segment must carry.

A device's Warden signs its trail segment with the node's own key, the one the registry pinned at
enrolment. The cases look the key up for a node the registry holds, a revoked one and a stranger,
and then run real signed, unsigned and forged exports through a receiver built on those keys.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/keys.py (codingrules section 3). The registry is the
    real in-memory one; the chunks are what the real sender cuts (`builders.segments`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.keys for the module under test.
    - tests.unit.queen.cell_gate.test_segment_keys for the Virtual Cell counterpart.
"""

from __future__ import annotations

import pytest
from builders.segments import SegmentRig, make_segment_rig, segment_chunks, signed
from builders.swarm import GateRig, enrol_node, make_device_party, make_swarm_node

from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.swarm_gate import RegistrySegmentKeys
from hivemind.queen.trail import (
    BadSegmentSignatureError,
    TrailSegmentReceiver,
    UnsignedSegmentError,
)
from hivemind.swarm import NodeStatus
from waggle.signing import Ed25519Signer


async def _enrolled(rig: GateRig) -> SegmentRig:
    """Enrol a device and return the ids and key a segment from it is made with."""
    clock = rig.clock
    party = make_device_party(clock)
    await enrol_node(rig.hive.registry, make_swarm_node(party, clock), clock)
    base = make_segment_rig(clock)
    return SegmentRig(
        clock=clock,
        hive_id=base.hive_id,
        cell_id=party.cell_id,
        node_id=party.node_id,
        warden_id=party.warden_id,
        signer=party.signer,
    )


async def test_a_node_the_registry_holds_has_its_pinned_key_as_a_required_one(
    rig: GateRig,
) -> None:
    segment = await _enrolled(rig)
    [chunk] = await segment_chunks(segment)

    key = await RegistrySegmentKeys(rig.hive.registry).key_for(chunk)

    assert key.public_key == segment.signer.public_key_bytes
    assert key.is_required is True  # A Swarm node's segment must always be signed.


async def test_a_revoked_node_has_no_key_left_but_must_still_sign(rig: GateRig) -> None:
    segment = await _enrolled(rig)
    [chunk] = await segment_chunks(segment)
    node = await rig.hive.registry.node_by_node_id(segment.node_id)
    assert node is not None
    await rig.hive.registry.set_status(
        node.device_id, node.status, NodeStatus.REVOKED, rig.clock.now()
    )

    key = await RegistrySegmentKeys(rig.hive.registry).key_for(chunk)

    assert key.public_key is None and key.is_required is True  # Refused, never merged unchecked.


async def test_a_node_the_registry_does_not_know_has_no_key_but_must_still_sign(
    rig: GateRig,
) -> None:
    [chunk] = await segment_chunks(make_segment_rig(rig.clock))

    key = await RegistrySegmentKeys(rig.hive.registry).key_for(chunk)

    assert key.public_key is None and key.is_required is True


def _receiver(rig: GateRig, trail: MemoryPheromoneTrail) -> TrailSegmentReceiver:
    """A receiver over `trail` that authenticates against the registry's keys."""
    return TrailSegmentReceiver(trail, keys=RegistrySegmentKeys(rig.hive.registry))


async def test_a_segment_signed_with_the_nodes_key_is_merged(rig: GateRig) -> None:
    segment = await _enrolled(rig)
    trail = MemoryPheromoneTrail(rig.clock)
    chunks = signed(await segment_chunks(segment), segment.signer)

    inserted = [await _receiver(rig, trail).receive(chunk) for chunk in chunks]

    assert inserted[-1] == 1
    assert len(await trail.query(TrailQuery(kind="cell.provisioned"))) == 1


async def test_an_unsigned_segment_from_a_swarm_node_is_refused_and_not_merged(
    rig: GateRig,
) -> None:
    segment = await _enrolled(rig)
    trail = MemoryPheromoneTrail(rig.clock)
    receiver = _receiver(rig, trail)

    with pytest.raises(UnsignedSegmentError):
        for chunk in await segment_chunks(segment):
            await receiver.receive(chunk)

    assert await trail.query(TrailQuery(kind="cell.provisioned")) == ()


async def test_a_segment_signed_with_another_key_is_refused_and_not_merged(rig: GateRig) -> None:
    segment = await _enrolled(rig)
    trail = MemoryPheromoneTrail(rig.clock)
    receiver = _receiver(rig, trail)
    forged = signed(await segment_chunks(segment), Ed25519Signer.generate())

    with pytest.raises(BadSegmentSignatureError):
        for chunk in forged:
            await receiver.receive(chunk)

    assert await trail.query(TrailQuery(kind="cell.provisioned")) == ()
