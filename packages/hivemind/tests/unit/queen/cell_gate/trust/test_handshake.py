"""Tests for hivemind.queen.cell_gate.trust.handshake: the verifier and the knocks it records.

`GateVerifier` checks a fresh node's first frame against the key registered for the Cell it
claims, refuses a frame naming no Cell, or a malformed one, as an unknown signer (never another
error the transport would not expect), and keeps what a failing frame claimed for the handshake to
record. `HandshakeRefusals` records at most `HANDSHAKE_RECORDS_PER_WINDOW` knocks per window, about
the Hive, and counts the ones it skipped on the next record. The same over a real socket is in
`test_listener`.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/trust/handshake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json

import pytest

from hivemind.pheromone import TrailQuery, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate import (
    HANDSHAKE_REFUSED_KIND,
    GateVerifier,
    HandshakeRefusals,
    QueenReadinessGate,
)
from hivemind.queen.cell_gate.trust.handshake import (
    HANDSHAKE_RECORDS_PER_WINDOW,
    HANDSHAKE_WINDOW_S,
)
from waggle.clock import FakeClock
from waggle.errors import InvalidSignatureError, UnknownSignerError
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer, public_key_hex

_ADDRESS = "127.0.0.1:50000"  # Where a test's knock came from.


def _frame(cell_id: str) -> bytes:
    """Canonical bytes of a first frame whose payload names `cell_id`."""
    return json.dumps({"payload": {"cell_id": cell_id}}, sort_keys=True).encode()


async def _gate_expecting(
    signer: Ed25519Signer, clock: FakeClock
) -> tuple[QueenReadinessGate, str]:
    """A gate that expects one fresh Cell under `signer`'s key; the gate and the Cell's id."""
    gate = QueenReadinessGate()
    cell_id = new_cell_id(clock)
    await gate.expect(cell_id, public_key_hex(signer.public_key_bytes))
    return gate, cell_id


async def test_a_first_frame_signed_by_its_cells_key_verifies() -> None:
    clock, signer = FakeClock(), Ed25519Signer.generate()
    gate, cell_id = await _gate_expecting(signer, clock)
    frame = _frame(cell_id)

    GateVerifier(gate).verify(new_node_id(clock), frame, signer.sign(frame))


async def test_a_first_frame_signed_by_another_key_is_refused() -> None:
    clock = FakeClock()
    gate, cell_id = await _gate_expecting(Ed25519Signer.generate(), clock)
    frame = _frame(cell_id)

    with pytest.raises(InvalidSignatureError):
        GateVerifier(gate).verify(new_node_id(clock), frame, Ed25519Signer.generate().sign(frame))


@pytest.mark.parametrize("cell_id", ["not-a-cell-id", "cell_0", ""])
async def test_a_malformed_cell_id_is_an_unknown_signer_not_a_crash(cell_id: str) -> None:
    clock, signer = FakeClock(), Ed25519Signer.generate()
    gate, _ = await _gate_expecting(signer, clock)
    frame = _frame(cell_id)

    with pytest.raises(UnknownSignerError):
        GateVerifier(gate).verify(new_node_id(clock), frame, signer.sign(frame))


async def test_knocks_past_the_window_limit_are_counted_on_the_next_record() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    hive_id = new_hive_id(clock)
    recorder = TrailRecorder(trail=trail, clock=clock, hive_id=hive_id, node_id=new_node_id(clock))
    refusals = HandshakeRefusals(recorder)
    error = InvalidSignatureError("A test knock.")

    for _ in range(HANDSHAKE_RECORDS_PER_WINDOW + 2):
        await refusals.refused(error, _ADDRESS)
    in_window = await trail.query(TrailQuery(kind=HANDSHAKE_REFUSED_KIND))
    clock.advance(HANDSHAKE_WINDOW_S)
    await refusals.refused(error, _ADDRESS)
    events = await trail.query(TrailQuery(kind=HANDSHAKE_REFUSED_KIND))

    assert len(in_window) == HANDSHAKE_RECORDS_PER_WINDOW
    assert len(events) == HANDSHAKE_RECORDS_PER_WINDOW + 1
    last = max(events, key=lambda event: event.id)
    assert (last.subject_id, last.payload["suppressed"]) == (hive_id, 2)
    # No verifier set a claim in this task: nothing is attributed to anyone.
    assert (last.payload["claimed_cell_id"], last.payload["claimed_node_id"]) == (None, None)


async def test_without_a_recorder_a_knock_is_only_logged() -> None:
    await HandshakeRefusals(None).refused(InvalidSignatureError("A test knock."), _ADDRESS)
