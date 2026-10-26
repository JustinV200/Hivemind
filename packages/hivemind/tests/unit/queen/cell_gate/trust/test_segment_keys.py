"""Tests for hivemind.queen.cell_gate.trust.segment_keys: a Virtual Cell's segment key, optional.

The readiness gate registers each provisioned Cell's public key before the Cell dials. A Virtual
Cell's trail segment need not be signed (its Warden never was required to), but one that arrives
signed is checked against that key, so a Warden cannot vouch for itself with another. The cases
look the key up for a Cell the gate holds and one it does not, and then run real signed and
unsigned exports through a receiver built on those keys.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/trust/segment_keys.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.trust.segment_keys for the module under test.
    - tests.unit.queen.trail.test_sync_signed for the receiver's check with a required key.
"""

from __future__ import annotations

import pytest
from builders.segments import make_segment_rig, segment_chunks, signed

from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate import GateSegmentKeys, QueenReadinessGate
from hivemind.queen.trail import BadSegmentSignatureError, TrailSegmentReceiver
from waggle.signing import Ed25519Signer, public_key_hex


async def test_a_cell_the_gate_expects_has_its_registered_key_as_an_optional_one() -> None:
    rig = make_segment_rig()
    gate = QueenReadinessGate()
    await gate.expect(rig.cell_id, public_key_hex(rig.signer.public_key_bytes))
    [chunk] = await segment_chunks(rig)

    key = await GateSegmentKeys(gate).key_for(chunk)

    assert key is not None
    assert key.public_key == rig.signer.public_key_bytes
    assert key.is_required is False


async def test_a_cell_the_gate_does_not_hold_has_no_key() -> None:
    rig = make_segment_rig()
    gate = QueenReadinessGate()
    [chunk] = await segment_chunks(rig)
    assert await GateSegmentKeys(gate).key_for(chunk) is None  # Never expected.

    await gate.expect(rig.cell_id, public_key_hex(rig.signer.public_key_bytes))
    await gate.forget(rig.cell_id)

    assert await GateSegmentKeys(gate).key_for(chunk) is None  # Expected once, then forgotten.


def _receiver(gate: QueenReadinessGate, trail: MemoryPheromoneTrail) -> TrailSegmentReceiver:
    """A receiver over `trail` that authenticates against the gate's keys."""
    return TrailSegmentReceiver(trail, keys=GateSegmentKeys(gate))


async def test_an_unsigned_virtual_cell_segment_still_merges() -> None:
    rig = make_segment_rig()
    gate, trail = QueenReadinessGate(), MemoryPheromoneTrail(rig.clock)
    await gate.expect(rig.cell_id, public_key_hex(rig.signer.public_key_bytes))
    receiver = _receiver(gate, trail)

    results = [await receiver.receive(chunk) for chunk in await segment_chunks(rig)]

    assert results == [1]
    assert len(await trail.query(TrailQuery(kind="cell.provisioned"))) == 1


async def test_a_segment_signed_by_the_cells_registered_key_merges() -> None:
    rig = make_segment_rig()
    gate, trail = QueenReadinessGate(), MemoryPheromoneTrail(rig.clock)
    await gate.expect(rig.cell_id, public_key_hex(rig.signer.public_key_bytes))
    receiver = _receiver(gate, trail)

    results = [await receiver.receive(c) for c in signed(await segment_chunks(rig), rig.signer)]

    assert results == [1]


async def test_a_segment_signed_by_any_other_key_is_refused_and_merges_nothing() -> None:
    rig = make_segment_rig()
    gate, trail = QueenReadinessGate(), MemoryPheromoneTrail(rig.clock)
    await gate.expect(rig.cell_id, public_key_hex(rig.signer.public_key_bytes))
    receiver = _receiver(gate, trail)
    forged = signed(await segment_chunks(rig), Ed25519Signer.generate())

    with pytest.raises(BadSegmentSignatureError):
        for chunk in forged:
            await receiver.receive(chunk)

    assert await trail.query(TrailQuery(kind="cell.provisioned")) == ()
