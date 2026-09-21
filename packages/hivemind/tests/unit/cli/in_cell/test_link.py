"""Tests for hivemind.cli.in_cell.link: CellLink -- connect, announce, heartbeat, stop on order.

Drives a real `WebSocketClientTransport`/`WebSocketServer` pair on loopback (roadmap step 5.5's
own test requirement: "driven end-to-end IN PROCESS against a WebSocket server on loopback with a
FakeClock where possible"), with the server side played by hand -- a raw `WebSocketServer` plus
manual envelope sends -- standing in for the Queen listener that does not exist yet (this
dispatch's own report names exactly what the Queen side is still missing).

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/link.py (codingrules section 3). Exercises the full sequence
    roadmap step 5.5 describes: connect out, send a signed CellReady, heartbeat on the injected
    clock's own interval, and stop on a `Shutdown`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.link for CellLink/CellLinkDeps, the module under test.
    - packages/waggle/tests/transport/test_websocket_client.py for the RecordingClock technique
      this module reuses to drive a heartbeat deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity

from hivemind.cell.models import Cell, CellKind
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.cli.in_cell.link import CellLink, CellLinkDeps
from waggle.clock import FakeClock
from waggle.codec import Codec, canonical_bytes
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    HiveId,
    NodeId,
    WardenId,
    new_cell_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.labels import Urgency
from waggle.signing import Ed25519Signer, Ed25519Verifier
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_client import WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
_HEARTBEAT_INTERVAL_S = 15.0  # An arbitrary, deliberately "long" interval: the point is that the
# test itself, not a real timer, decides when it elapses (RecordingClock below).


class RecordingClock(FakeClock):
    """A FakeClock that records every sleep and flags when a sleeper is waiting.

    Mirrors packages/waggle/tests/transport/test_websocket_client.py's own helper: lets this test
    wait until CellLink has actually registered its heartbeat sleep before advancing past it,
    rather than guessing how many event-loop turns that takes.
    """

    def __init__(self) -> None:
        """Create a RecordingClock with no sleeps recorded yet."""
        super().__init__()
        self.sleep_durations: list[float] = []
        self.asleep = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        """Record `seconds`, wake the advancing test, then sleep as a FakeClock does."""
        self.sleep_durations.append(seconds)
        self.asleep.set()
        await super().sleep(seconds)


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
    server = WebSocketServer(Codec())  # No signer/verifier: this test double reads what arrives
    # and builds its own signed replies by hand (see test body); it never needs to sign anything
    # itself with this particular codec instance.
    await server.start()
    return server


@dataclass(frozen=True, slots=True)
class _Scenario:
    """Everything one test needs once a CellLink has connected and announced itself."""

    link: CellLink
    cell: Cell
    clock: RecordingClock
    cell_signer: Ed25519Signer
    hive_id: HiveId
    queen_node_id: NodeId
    warden_id: WardenId
    server_transport: WebSocketTransport
    server_receive: AsyncIterator[Envelope]


async def _connect_and_announce(server: WebSocketServer) -> _Scenario:
    """Build a CellLink, connect it to `server`, and return every handle a test needs next."""
    cell_signer = Ed25519Signer.generate()
    queen_signer = Ed25519Signer.generate()
    cell_node_id = new_node_id(FakeClock())
    queen_node_id = new_node_id(FakeClock())
    hive_id = new_hive_id(FakeClock())
    warden_id = new_warden_id(FakeClock())
    client_codec = Codec(
        signer=cell_signer,
        verifier=Ed25519Verifier({queen_node_id: queen_signer.public_key_bytes}),
    )
    clock = RecordingClock()
    cell = _cell()
    link = CellLink(
        CellLinkDeps(
            transport=WebSocketClientTransport(server.uri, client_codec, clock),
            cell=cell,
            warden_id=warden_id,
            hive_id=hive_id,
            node_id=cell_node_id,
            clock=clock,
            heartbeat_interval_s=_HEARTBEAT_INTERVAL_S,
            runtime_version="0.1.0-test",
        )
    )

    connections = server.connections()
    await link.announce()
    server_transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
    return _Scenario(
        link=link,
        cell=cell,
        clock=clock,
        cell_signer=cell_signer,
        hive_id=hive_id,
        queen_node_id=queen_node_id,
        warden_id=warden_id,
        server_transport=server_transport,
        server_receive=server_transport.receive(),
    )


async def test_cell_link_connects_announces_heartbeats_and_stops_on_shutdown(
    _server: WebSocketServer,
) -> None:
    scenario = await _connect_and_announce(_server)

    # 1. Connects out and sends a signed CellReady.
    ready_envelope = await asyncio.wait_for(anext(scenario.server_receive), timeout=WAIT_S)
    assert isinstance(ready_envelope.payload, CellReady)
    assert ready_envelope.payload.cell_id == scenario.cell.id
    assert ready_envelope.payload.warden_id == scenario.warden_id
    _assert_signed_by(ready_envelope, scenario.cell_signer)

    run_task = asyncio.ensure_future(scenario.link.run())

    # 2. Heartbeats once the injected clock's own interval elapses.
    await scenario.clock.asleep.wait()
    scenario.clock.asleep.clear()
    scenario.clock.advance(_HEARTBEAT_INTERVAL_S)
    heartbeat_envelope = await asyncio.wait_for(anext(scenario.server_receive), timeout=WAIT_S)
    assert isinstance(heartbeat_envelope.payload, CellHeartbeat)
    assert heartbeat_envelope.payload.cell_id == scenario.cell.id
    _assert_signed_by(heartbeat_envelope, scenario.cell_signer)

    # 3. Stops on a Shutdown from the Queen's side of the link.
    shutdown_hop = Hop(
        sender=scenario.hive_id, recipient=scenario.warden_id, node_id=scenario.queen_node_id
    )
    shutdown = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
    await scenario.server_transport.send(wrap(shutdown, shutdown_hop, clock=scenario.clock))
    await asyncio.wait_for(run_task, timeout=WAIT_S)

    await scenario.link.close()
    await _server.close()


async def test_close_reaps_every_task_it_still_owns(_server: WebSocketServer) -> None:
    clock = RecordingClock()
    connections = _server.connections()
    link = CellLink(
        CellLinkDeps(
            transport=WebSocketClientTransport(_server.uri, Codec(), clock),
            cell=_cell(),
            warden_id=new_warden_id(FakeClock()),
            hive_id=new_hive_id(FakeClock()),
            node_id=new_node_id(FakeClock()),
            clock=clock,
            heartbeat_interval_s=_HEARTBEAT_INTERVAL_S,
            runtime_version="0.1.0-test",
        )
    )
    await link.announce()
    server_transport: WebSocketTransport = await asyncio.wait_for(
        anext(connections), timeout=WAIT_S
    )
    await anext(server_transport.receive())  # Drain the CellReady so close() below is clean.

    run_task = asyncio.ensure_future(link.run())
    await clock.asleep.wait()  # The heartbeat deadline task is now pending, and so is a receive.
    link.stop()

    await asyncio.wait_for(run_task, timeout=WAIT_S)
    await link.close()  # Must not hang or raise: both owned tasks are reaped cleanly.

    await _server.close()
