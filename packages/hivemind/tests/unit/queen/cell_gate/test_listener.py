"""Unit tests for hivemind.queen.cell_gate.listener: CellListener.

Drives a real `WebSocketClientTransport`/`CellListener` pair on loopback, the same in-process
WebSocket technique `tests/unit/cli/in_cell/test_link.py` uses for `CellLink` (its own module
docstring names it as the reusable pattern): a real "Cell" client dials the listener, sends a
signed `CellReady` then `CellHeartbeat`, and this module checks the listener attaches a
`WardenLink` to the given `Queen` only once both have verified, and detaches it when the
connection ends.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/cell_gate/listener.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.listener for CellListener, the class under test.
    - tests/unit/cli/in_cell/test_link.py for the in-process WebSocket driving technique reused.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from builders.cells import make_capabilities
from builders.queen import make_queen_deps

from hivemind.pheromone import TrailQuery
from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.listener import CellListener, CellListenerDeps
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from hivemind.queen.trail import TrailSegmentReceiver
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import (
    CellId,
    HiveId,
    NodeId,
    WardenId,
    new_cell_id,
    new_event_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)
from waggle.messages.cell.status import CellHeartbeat, CellMode, CellReady
from waggle.messages.labels import AccessLevel as WireAccessLevel
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.messages.swarm import TrailSegmentSync
from waggle.signing import Ed25519Signer, Ed25519Verifier, public_key_hex
from waggle.transport.memory import MemoryTransport
from waggle.transport.websocket_client import WebSocketClientTransport

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.


def _cell_ready(cell_id: CellId, warden_id: WardenId) -> CellReady:
    """Build a valid CellReady for `cell_id`, the shape a real Cell's own CellLink sends."""
    platform, capabilities = make_capabilities().to_wire()
    return CellReady(
        cell_id=cell_id,
        warden_id=warden_id,
        platform=platform,
        capabilities=capabilities,
        access_level=WireAccessLevel.FULL,
        comb_shield=WireCombShieldLevel.MEADOW,
        attestation=(),
        runtime_version="0.1.0-test",
    )


class _FakeQueenCell:
    """Everything one test scenario needs to act as one Virtual Cell dialling the listener."""

    def __init__(self, listener: CellListener) -> None:
        self.cell_id = new_cell_id(FakeClock())
        self.warden_id = new_warden_id(FakeClock())
        self.node_id = new_node_id(FakeClock())
        self.signer = Ed25519Signer.generate()
        self._listener = listener

    async def dial(
        self, queen_node_id: NodeId, queen_signer: Ed25519Signer
    ) -> WebSocketClientTransport:
        """Connect to the listener with a codec that only trusts the Queen's own key."""
        codec = Codec(
            signer=self.signer,
            verifier=Ed25519Verifier({queen_node_id: queen_signer.public_key_bytes}),
        )
        transport = WebSocketClientTransport(self._listener.uri, codec, FakeClock())
        await transport.connect()
        return transport

    def _hop(self, hive_id: HiveId) -> Hop:
        return Hop(sender=self.warden_id, recipient=hive_id, node_id=self.node_id)

    async def send_ready(self, transport: WebSocketClientTransport, hive_id: HiveId) -> None:
        ready = _cell_ready(self.cell_id, self.warden_id)
        await transport.send(wrap(ready, self._hop(hive_id), clock=FakeClock()))

    async def send_heartbeat(self, transport: WebSocketClientTransport, hive_id: HiveId) -> None:
        heartbeat = CellHeartbeat(
            cell_id=self.cell_id,
            mode=CellMode.ACTIVE,
            lease_ids=(),
            worker_count=0,
            is_shield_verified=True,
            interval_s=15.0,
        )
        await transport.send(wrap(heartbeat, self._hop(hive_id), clock=FakeClock()))


@dataclass(frozen=True, slots=True)
class _Scenario:
    """A started CellListener, its own Queen, gate, trail and the ids a test dials it as."""

    listener: CellListener
    queen: Queen
    gate: QueenReadinessGate
    queen_signer: Ed25519Signer
    queen_node_id: NodeId
    hive_id: HiveId
    trail: MemoryPheromoneTrail


async def _build_scenario() -> _Scenario:
    """Build one started CellListener attached to one fresh Queen, sharing one hive_id."""
    deps: QueenDeps
    deps, _link, _end = make_queen_deps()
    gate = QueenReadinessGate()
    queen_signer = Ed25519Signer.generate()
    queen_node_id = new_node_id(FakeClock())
    listener = CellListener(
        CellListenerDeps(
            gate=gate,
            queen_signer=queen_signer,
            queen_node_id=queen_node_id,
            hive_id=deps.identity.hive_id,
        ),
        FakeClock(),
    )
    queen = Queen(deps)
    await listener.start(queen)
    assert isinstance(deps.trail, MemoryPheromoneTrail)
    return _Scenario(
        listener, queen, gate, queen_signer, queen_node_id, deps.identity.hive_id, deps.trail
    )


async def _attach(scenario: _Scenario) -> tuple[_FakeQueenCell, WebSocketClientTransport]:
    """Dial, verify and attach one fake Cell to `scenario`'s own listener; return it, connected."""
    cell = _FakeQueenCell(scenario.listener)
    await scenario.gate.expect(cell.cell_id, public_key_hex(cell.signer.public_key_bytes))
    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer)
    await cell.send_ready(transport, scenario.hive_id)
    await cell.send_heartbeat(transport, scenario.hive_id)
    await asyncio.wait_for(_until(lambda: cell.warden_id in _attached_ids(scenario.queen)), WAIT_S)
    return cell, transport


async def test_listener_attaches_a_warden_link_once_ready_and_heartbeat_arrive() -> None:
    scenario = await _build_scenario()
    cell = _FakeQueenCell(scenario.listener)
    await scenario.gate.expect(cell.cell_id, public_key_hex(cell.signer.public_key_bytes))

    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer)
    await cell.send_ready(transport, scenario.hive_id)
    await asyncio.sleep(0.05)  # Let the listener's own handler task drain the CellReady frame.
    assert cell.warden_id not in _attached_ids(scenario.queen)  # Not yet: no heartbeat.

    await cell.send_heartbeat(transport, scenario.hive_id)
    await asyncio.wait_for(_until(lambda: cell.warden_id in _attached_ids(scenario.queen)), WAIT_S)

    assert scenario.gate.node_id_for(cell.cell_id) == cell.node_id

    await transport.close()
    await asyncio.wait_for(
        _until(lambda: cell.warden_id not in _attached_ids(scenario.queen)), WAIT_S
    )
    await scenario.listener.stop()


async def test_listener_never_attaches_an_unexpected_cell() -> None:
    scenario = await _build_scenario()
    cell = _FakeQueenCell(scenario.listener)
    # Deliberately never gate.expect()-ed: the listener must refuse this Cell's key lookup.

    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer)
    await cell.send_ready(transport, scenario.hive_id)
    await asyncio.sleep(0.05)

    assert scenario.queen.wardens == ()
    await transport.close()
    await scenario.listener.stop()


class _FakeSnapshotHandler:
    """A CellSnapshotHandler stand-in.

    This module tests the listener's own dispatch, not a real backend
    (hivemind.queen.cell_gate.snapshot's own module has that coverage).
    """

    async def snapshot(self, request: CellSnapshotRequest) -> CellSnapshotReply:
        return CellSnapshotReply(cell_id=request.cell_id, snapshot_id="snap_test", error=None)

    async def rollback(self, request: CellRollbackRequest) -> CellRollbackReply:
        return CellRollbackReply(cell_id=request.cell_id, ok=True, error=None)


async def test_listener_answers_a_snapshot_request_correlated_to_its_own_envelope() -> None:
    scenario = await _build_scenario()
    scenario.listener.bind_snapshot_handler(_FakeSnapshotHandler())
    cell, transport = await _attach(scenario)

    request = CellSnapshotRequest(cell_id=cell.cell_id, purpose="test")
    envelope = wrap(request, cell._hop(scenario.hive_id), clock=FakeClock())
    await transport.send(envelope)
    reply_envelope = await asyncio.wait_for(anext(transport.receive()), WAIT_S)

    assert isinstance(reply_envelope.payload, CellSnapshotReply)
    assert reply_envelope.correlation_id == envelope.id
    assert reply_envelope.payload.snapshot_id == "snap_test"

    await transport.close()
    await scenario.listener.stop()


async def test_listener_merges_a_trail_segment_sync_into_the_queens_trail() -> None:
    scenario = await _build_scenario()
    scenario.listener.bind_trail_receiver(TrailSegmentReceiver(scenario.trail))
    cell, transport = await _attach(scenario)

    clock = FakeClock()
    source_trail = MemoryPheromoneTrail(clock)
    event = CellEvent(
        id=new_event_id(clock),
        hive_id=scenario.hive_id,
        node_id=cell.node_id,
        at=clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=cell.cell_id,
        payload={},
    )
    await source_trail.record(event)
    chunk = await _one_trail_segment_sync_chunk(clock, cell, source_trail, scenario.hive_id)
    await transport.send(wrap(chunk, cell._hop(scenario.hive_id), clock=clock))

    async def _merged() -> bool:
        return bool(await scenario.trail.query(TrailQuery(kind="cell.provisioned")))

    await asyncio.wait_for(_until_async(_merged), WAIT_S)

    await transport.close()
    await scenario.listener.stop()


async def _one_trail_segment_sync_chunk(
    clock: FakeClock, cell: _FakeQueenCell, source_trail: MemoryPheromoneTrail, hive_id: HiveId
) -> TrailSegmentSync:
    """Sync `source_trail` over a real WaggleTrailSync and return its one chunk (small export)."""
    sender_transport, receiver_transport = MemoryTransport.pair(Codec(), Codec())
    sync = WaggleTrailSync(
        TrailSyncDeps(
            trail=source_trail,
            transport=sender_transport,
            node_id=cell.node_id,
            cell_id=cell.cell_id,
            warden_id=cell.warden_id,
            hive_id=hive_id,
            clock=clock,
        )
    )
    await sync.sync()
    await receiver_transport.close()
    chunks = [
        envelope.payload
        async for envelope in receiver_transport.receive()
        if isinstance(envelope.payload, TrailSegmentSync)
    ]
    assert len(chunks) == 1  # One small event fits in one chunk (MAX_CHUNK_BYTES).
    return chunks[0]


def _attached_ids(queen: Queen) -> set[WardenId]:
    """Return every currently attached WardenId, for a before/after membership check."""
    return {link.warden_id for link in queen.wardens}


async def _until(condition: object) -> None:
    """Poll `condition` (a zero-arg callable) until it is true.

    A short poll, not an `asyncio.Event`, on purpose: the state being waited on
    (`queen.wardens`) changes inside `CellListener`'s own handler task, which this test does not
    instrument with an event of its own; every call site bounds this with `asyncio.wait_for`.
    """
    assert callable(condition)
    while not condition():  # noqa: ASYNC110 - polling shared state, see docstring.
        await asyncio.sleep(0.01)


async def _until_async(condition: object) -> None:
    """Same as `_until`, for a zero-arg async condition (an `await`-ing query, not shared state)."""
    assert callable(condition)
    while not await condition():  # noqa: ASYNC110 - polling shared state, see _until's docstring.
        await asyncio.sleep(0.01)
