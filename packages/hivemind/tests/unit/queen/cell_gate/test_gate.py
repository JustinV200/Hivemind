"""Unit tests for hivemind.queen.cell_gate.gate: QueenReadinessGate.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/cell_gate/gate.py
    (codingrules section 3). Checks the ReadinessGate contract (ready only after resolve(), a
    timeout when it never comes, idempotent forget) plus the cell_id-keyed lookups
    hivemind.queen.cell_gate.listener's own _GateVerifier depends on.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.gate for QueenReadinessGate, the class under test.
    - hivemind.hive.backends.fake for FakeReadinessGate, the contract this mirrors.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity

from hivemind.hive.backends.bootstrap import CellReadyInfo
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from waggle.clock import FakeClock
from waggle.ids import CellId, new_node_id
from waggle.signing import Ed25519Signer, public_key_hex

WAIT_S = 5.0  # Bounds every await that could hang.


def _info() -> CellReadyInfo:
    return CellReadyInfo(capabilities=make_capabilities(), capacity=make_capacity())


async def test_expected_key_is_none_before_expect() -> None:
    gate = QueenReadinessGate()

    assert gate.expected_key(CellId("cell_unknown")) is None


async def test_expect_registers_the_key_expected_key_returns() -> None:
    gate = QueenReadinessGate()
    cell_id = CellId("cell_test")
    key_hex = public_key_hex(Ed25519Signer.generate().public_key_bytes)

    await gate.expect(cell_id, key_hex)

    assert gate.expected_key(cell_id) == key_hex


async def test_wait_ready_blocks_until_resolve() -> None:
    gate = QueenReadinessGate()
    cell_id = CellId("cell_test")
    key_hex = public_key_hex(Ed25519Signer.generate().public_key_bytes)
    await gate.expect(cell_id, key_hex)

    wait_task = asyncio.ensure_future(gate.wait_ready(cell_id, timeout_s=WAIT_S))
    await asyncio.sleep(0)  # Let wait_ready actually start waiting on the event.
    assert not wait_task.done()

    resolved_node_id = new_node_id(FakeClock())
    gate.resolve(cell_id, resolved_node_id, _info())

    info = await asyncio.wait_for(wait_task, timeout=WAIT_S)
    assert info == _info()
    assert gate.node_id_for(cell_id) == resolved_node_id


async def test_wait_ready_times_out_when_never_resolved() -> None:
    gate = QueenReadinessGate()
    cell_id = CellId("cell_test")
    await gate.expect(cell_id, public_key_hex(Ed25519Signer.generate().public_key_bytes))

    with pytest.raises(TimeoutError):
        await gate.wait_ready(cell_id, timeout_s=0.01)


async def test_wait_ready_on_an_unexpected_cell_raises_immediately() -> None:
    gate = QueenReadinessGate()

    with pytest.raises(TimeoutError):
        await gate.wait_ready(CellId("cell_never_expected"), timeout_s=5.0)


async def test_forget_clears_every_registration() -> None:
    gate = QueenReadinessGate()
    cell_id = CellId("cell_test")
    await gate.expect(cell_id, public_key_hex(Ed25519Signer.generate().public_key_bytes))

    await gate.forget(cell_id)

    assert gate.expected_key(cell_id) is None
    with pytest.raises(TimeoutError):
        await gate.wait_ready(cell_id, timeout_s=0.01)


async def test_forget_is_idempotent() -> None:
    gate = QueenReadinessGate()

    await gate.forget(CellId("cell_never_expected"))  # Must not raise.
    await gate.forget(CellId("cell_never_expected"))  # Twice: still must not raise.


async def test_node_id_for_is_none_before_resolve() -> None:
    gate = QueenReadinessGate()

    assert gate.node_id_for(CellId("cell_test")) is None
