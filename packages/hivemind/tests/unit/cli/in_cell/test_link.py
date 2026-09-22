"""Tests for hivemind.cli.in_cell.link: announce, send_capacity_report, send_cell_heartbeat.

Drives a real `WebSocketClientTransport`/`WebSocketServer` pair on loopback (roadmap step 5.5's
own test requirement: "driven end-to-end IN PROCESS against a WebSocket server on loopback"), with
the server side played by hand -- a raw `WebSocketServer` plus manual envelope reads -- standing in
for the Queen listener's own readiness gate (`hivemind.queen.cell_gate.listener.CellListener`,
unmodified by this dispatch), which waits for exactly `CellReady` then `CellHeartbeat`.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/link.py (codingrules section 3). Exercises the sequence this
    dispatch's own module docstring describes: connect out, send a signed CellReady, a
    CapacityReport, then one CellHeartbeat -- the three frames that must go out before a real
    `hivemind.wardens.warden.Warden` exists at all (see test_main.py for the Warden itself).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.link for CellLinkDeps/announce/send_capacity_report/
      send_cell_heartbeat, the module under test.
    - test_main.py for the full entry point, Warden included, over the same kind of transport.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity

from hivemind.cell.models import Cell, CellKind
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.cli.in_cell.link import (
    CellLinkDeps,
    announce,
    send_capacity_report,
    send_cell_heartbeat,
)
from waggle.clock import FakeClock
from waggle.codec import Codec, canonical_bytes
from waggle.envelope import Envelope
from waggle.ids import new_cell_id, new_hive_id, new_node_id, new_warden_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.forage import CapacityReport, CapacityTrigger
from waggle.signing import Ed25519Signer, Ed25519Verifier
from waggle.transport.websocket_client import WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.


def _cell() -> Cell:
    """A terminal-only Virtual Cell, the same shape InCellSpawnSource.cells() would report."""
    return Cell(
        id=new_cell_id(FakeClock()),
        kind=CellKind.VIRTUAL,
        name="test-cell",
        source="in_cell",
        capabilities=make_capabilities(),
        capacity=make_capacity(),
        access_level=AccessLevel.FULL,
        comb_shield=CombShieldLevel.MEADOW,
    )


def _assert_signed_by(envelope: Envelope, signer: Ed25519Signer) -> None:
    """Independently re-verify `envelope`'s signature against `signer`'s own public key."""
    assert envelope.signature is not None
    verifier = Ed25519Verifier({envelope.node_id: signer.public_key_bytes})
    wire = envelope.model_dump(mode="json", exclude={"signature"})
    verifier.verify(envelope.node_id, canonical_bytes(wire), envelope.signature)


@pytest.fixture
async def _server() -> WebSocketServer:
    server = WebSocketServer(Codec())  # No signer/verifier: this test double only reads what
    # arrives; it never sends anything itself, so it never needs to sign.
    await server.start()
    return server


def _deps(server: WebSocketServer, cell_signer: Ed25519Signer) -> CellLinkDeps:
    clock = FakeClock()
    return CellLinkDeps(
        transport=WebSocketClientTransport(server.uri, Codec(signer=cell_signer), clock),
        cell=_cell(),
        warden_id=new_warden_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        clock=clock,
        heartbeat_interval_s=15.0,
        runtime_version="0.1.0-test",
    )


async def test_announce_connects_and_sends_a_signed_cell_ready_first(
    _server: WebSocketServer,
) -> None:
    cell_signer = Ed25519Signer.generate()
    deps = _deps(_server, cell_signer)
    connections = _server.connections()

    await announce(deps)

    server_transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
    envelope = await asyncio.wait_for(anext(server_transport.receive()), timeout=WAIT_S)
    assert isinstance(envelope.payload, CellReady)
    assert envelope.payload.cell_id == deps.cell.id
    assert envelope.payload.warden_id == deps.warden_id
    _assert_signed_by(envelope, cell_signer)

    await deps.transport.close()
    await _server.close()


async def test_send_capacity_report_carries_the_probed_forage_capacity(
    _server: WebSocketServer,
) -> None:
    cell_signer = Ed25519Signer.generate()
    deps = _deps(_server, cell_signer)
    connections = _server.connections()
    await announce(deps)
    server_transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
    server_receive = server_transport.receive()
    await anext(server_receive)  # Drain CellReady: this test only checks the report after it.

    capacity = make_capacity(max_sub_bees=7)
    await send_capacity_report(deps, capacity)

    envelope = await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)
    assert isinstance(envelope.payload, CapacityReport)
    assert envelope.payload.cell_id == deps.cell.id
    assert envelope.payload.trigger is CapacityTrigger.PROVISIONED
    assert envelope.payload.max_sub_bees == 7
    assert envelope.payload.host == capacity.host.to_wire()
    _assert_signed_by(envelope, cell_signer)

    await deps.transport.close()
    await _server.close()


async def test_send_cell_heartbeat_reports_active_with_no_leases_or_workers(
    _server: WebSocketServer,
) -> None:
    cell_signer = Ed25519Signer.generate()
    deps = _deps(_server, cell_signer)
    connections = _server.connections()
    await announce(deps)
    server_transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
    server_receive = server_transport.receive()
    await anext(server_receive)  # Drain CellReady.

    await send_cell_heartbeat(deps)

    envelope = await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)
    assert isinstance(envelope.payload, CellHeartbeat)
    assert envelope.payload.cell_id == deps.cell.id
    assert envelope.payload.lease_ids == ()
    assert envelope.payload.worker_count == 0
    assert envelope.payload.is_shield_verified is True  # MEADOW: always true (module docstring).
    assert envelope.payload.interval_s == deps.heartbeat_interval_s
    _assert_signed_by(envelope, cell_signer)

    await deps.transport.close()
    await _server.close()


async def test_the_full_sequence_arrives_ready_then_capacity_then_heartbeat(
    _server: WebSocketServer,
) -> None:
    """The order `hivemind.cli.in_cell.main` sends these in, and CellListener's gate relies on."""
    cell_signer = Ed25519Signer.generate()
    deps = _deps(_server, cell_signer)
    connections = _server.connections()

    await announce(deps)
    await send_capacity_report(deps, make_capacity())
    await send_cell_heartbeat(deps)

    server_transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
    server_receive = server_transport.receive()
    kinds = [
        type((await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)).payload).__name__
        for _ in range(3)
    ]
    assert kinds == ["CellReady", "CapacityReport", "CellHeartbeat"]

    await deps.transport.close()
    await _server.close()
