"""Unit tests for hivemind.queen.cell_gate.listener: CellListener.

Drives a real `WebSocketClientTransport`/`CellListener` pair on loopback, the same in-process
WebSocket technique `tests/unit/cli/in_cell/test_link.py` uses for `CellLink` (its own module
docstring names it as the reusable pattern): a real "Cell" client dials the listener, sends a
signed `CellReady` then `CellHeartbeat`, and this module checks the listener attaches a
`WardenLink` to the given `Queen` only once both have verified, and detaches it when the
connection ends. Roadmap step 10.6: a frame that fails its signature on an attached link closes it
and is `guard.envelope_refused` on the Queen's trail, and a segment chunk shipped as another node
is refused (`guard.segment_refused`) while the link stays up and its Warden is answered with a
`control.error` naming the reason; the Guard Bee reads that refusal and files a request through the
running Queen's own door, and she isolates the Cell by rule. A handshake frame that fails its
signature before attach is `guard.handshake_refused` about the Hive, with what it claimed kept
apart, never about the Cell it named. Roadmap
steps 11.3a and 11.3b: the transport's own limits are on, so a frame flood closes the link (code
4029) and a replayed envelope id is dropped, each recorded as a guard event naming the Cell's node.

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

import pytest
from builders.cell_listener import (
    WAIT_S,
    FakeCell,
    attach,
    attached_ids,
    build_scenario,
    recorded,
    until,
    until_async,
)
from builders.guard_bee import guard_bee_for_queen

from hivemind.pheromone import TrailQuery
from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.trail import TrailSegmentReceiver
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from hivemind.workers.roles.guard_bee import Disposition
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import wrap
from waggle.ids import (
    HiveId,
    new_event_id,
    new_node_id,
)
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)
from waggle.messages.cell.status import CellHeartbeat, CellMode
from waggle.messages.control import ErrorMessage
from waggle.messages.swarm import TrailSegmentSync
from waggle.signing import Ed25519Signer, public_key_hex
from waggle.transport.ceiling import DEFAULT_CEILING_LIMITS
from waggle.transport.memory import MemoryTransport

# The frames a connection may send at once before the ceiling closes it (waggle's own default).
CEILING_BURST_FRAMES = DEFAULT_CEILING_LIMITS.burst


async def test_listener_attaches_a_warden_link_once_ready_and_heartbeat_arrive() -> None:
    scenario = await build_scenario()
    cell = FakeCell(scenario.listener)
    await scenario.gate.expect(cell.cell_id, public_key_hex(cell.signer.public_key_bytes))

    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer)
    await cell.send_ready(transport, scenario.hive_id)
    await asyncio.sleep(0.05)  # Let the listener's own handler task drain the CellReady frame.
    assert cell.warden_id not in attached_ids(scenario.queen)  # Not yet: no heartbeat.

    await cell.send_heartbeat(transport, scenario.hive_id)
    await asyncio.wait_for(until(lambda: cell.warden_id in attached_ids(scenario.queen)), WAIT_S)

    assert scenario.gate.node_id_for(cell.cell_id) == cell.node_id
    # The Queen records the node the handshake proved, for the Guard Bee (roadmap step 10.6).
    [spawned] = await scenario.trail.query(TrailQuery(kind="warden.spawned"))
    assert spawned.payload == {"cell_id": cell.cell_id, "node_id": cell.node_id}

    await transport.close()
    await asyncio.wait_for(
        until(lambda: cell.warden_id not in attached_ids(scenario.queen)), WAIT_S
    )
    await scenario.listener.stop()


async def test_listener_never_attaches_an_unexpected_cell() -> None:
    scenario = await build_scenario()
    cell = FakeCell(scenario.listener)
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
    scenario = await build_scenario()
    scenario.listener.bind_snapshot_handler(_FakeSnapshotHandler())
    cell, transport = await attach(scenario)

    request = CellSnapshotRequest(cell_id=cell.cell_id, purpose="test")
    envelope = wrap(request, cell.hop(scenario.hive_id), clock=FakeClock())
    await transport.send(envelope)
    reply_envelope = await asyncio.wait_for(anext(transport.receive()), WAIT_S)

    assert isinstance(reply_envelope.payload, CellSnapshotReply)
    assert reply_envelope.correlation_id == envelope.id
    assert reply_envelope.payload.snapshot_id == "snap_test"

    await transport.close()
    await scenario.listener.stop()


async def test_listener_merges_a_trail_segment_sync_into_the_queens_trail() -> None:
    scenario = await build_scenario()
    scenario.listener.bind_trail_receiver(TrailSegmentReceiver(scenario.trail))
    cell, transport = await attach(scenario)

    clock = FakeClock()
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_provisioned(clock, cell, scenario.hive_id))
    chunk = await _one_trail_segment_sync_chunk(clock, cell, source_trail, scenario.hive_id)
    await transport.send(wrap(chunk, cell.hop(scenario.hive_id), clock=clock))

    async def _merged() -> bool:
        return bool(await scenario.trail.query(TrailQuery(kind="cell.provisioned")))

    await asyncio.wait_for(until_async(_merged), WAIT_S)

    await transport.close()
    await scenario.listener.stop()


async def _one_trail_segment_sync_chunk(
    clock: FakeClock, cell: FakeCell, source_trail: MemoryPheromoneTrail, hive_id: HiveId
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


class _Forger:
    """A Cell's signer whose key a test swaps mid-link, as a tampered link or impostor would."""

    def __init__(self, key: Ed25519Signer) -> None:
        self.key = key

    def sign(self, canonical: bytes) -> str:
        return self.key.sign(canonical)


async def test_a_forged_frame_on_an_attached_link_closes_it_and_is_recorded() -> None:
    scenario = await build_scenario()
    cell = FakeCell(scenario.listener)
    await scenario.gate.expect(cell.cell_id, public_key_hex(cell.signer.public_key_bytes))
    forger = _Forger(cell.signer)
    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer, forger)
    await cell.send_ready(transport, scenario.hive_id)
    await cell.send_heartbeat(transport, scenario.hive_id)
    await asyncio.wait_for(until(lambda: cell.warden_id in attached_ids(scenario.queen)), WAIT_S)

    forger.key = Ed25519Signer.generate()  # Signed as the Cell's node, with another key.
    await cell.send_heartbeat(transport, scenario.hive_id)
    await asyncio.wait_for(
        until(lambda: cell.warden_id not in attached_ids(scenario.queen)), WAIT_S
    )

    [refused] = await scenario.trail.query(TrailQuery(kind="guard.envelope_refused"))
    assert (refused.subject_id, refused.payload["reason"]) == (cell.cell_id, "invalid")
    assert refused.payload["node_id"] == cell.node_id
    await transport.close()
    await scenario.listener.stop()


async def test_a_segment_shipped_as_another_node_is_refused_and_the_link_stays_up() -> None:
    scenario = await build_scenario()
    scenario.listener.bind_trail_receiver(TrailSegmentReceiver(scenario.trail))
    cell, transport = await attach(scenario)
    clock = FakeClock()
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_provisioned(clock, cell, scenario.hive_id))
    chunk = await _one_trail_segment_sync_chunk(clock, cell, source_trail, scenario.hive_id)
    forged = chunk.model_copy(update={"node_id": new_node_id(clock)})

    forged_envelope = wrap(forged, cell.hop(scenario.hive_id), clock=clock)
    await transport.send(forged_envelope)
    reply = await asyncio.wait_for(anext(transport.receive()), WAIT_S)
    await transport.send(wrap(chunk, cell.hop(scenario.hive_id), clock=clock))  # Then its own.

    async def _merged() -> bool:
        return bool(await scenario.trail.query(TrailQuery(kind="cell.provisioned")))

    await asyncio.wait_for(until_async(_merged), WAIT_S)
    [refused] = await scenario.trail.query(TrailQuery(kind="guard.segment_refused"))
    assert (refused.subject_id, refused.payload["reason"]) == (cell.cell_id, "another_node")
    assert cell.warden_id in attached_ids(scenario.queen)  # A refused segment cuts no link.
    # The Cell's Warden is told, correlated to the refused chunk, with the reason in the code.
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.correlation_id == forged_envelope.id
    assert reply.payload.code == "hivemind.queen.segment_refused.another_node"
    assert (reply.payload.failed_kind, reply.payload.is_retryable) == (
        "swarm.trail_segment_sync",
        False,
    )
    await transport.close()
    await scenario.listener.stop()


@pytest.mark.parametrize(
    ("expected", "impostor", "reason"),
    [
        (True, True, "invalid"),  # A real, expected Cell's id, signed with a key not the Cell's.
        (False, False, "unknown_node"),  # A Cell never expected: no key to check it against.
    ],
)
async def test_a_failed_handshake_is_recorded_about_the_hive_never_the_cell_it_named(
    expected: bool, impostor: bool, reason: str
) -> None:
    scenario = await build_scenario()
    cell = FakeCell(scenario.listener)
    if expected:
        await scenario.gate.expect(cell.cell_id, public_key_hex(cell.signer.public_key_bytes))
    signer = _Forger(Ed25519Signer.generate()) if impostor else None
    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer, signer)

    await cell.send_ready(transport, scenario.hive_id)
    await asyncio.wait_for(
        until_async(lambda: recorded(scenario, "guard.handshake_refused")), WAIT_S
    )

    [refused] = await scenario.trail.query(TrailQuery(kind="guard.handshake_refused"))
    assert refused.subject_id == scenario.hive_id  # Nothing proved the Cell: it is not blamed.
    payload = refused.payload
    assert (payload["claimed_cell_id"], payload["claimed_node_id"]) == (cell.cell_id, cell.node_id)
    assert payload["reason"] == reason
    assert str(payload["address"]).startswith("127.0.0.1:")
    assert attached_ids(scenario.queen) == set()
    assert not await recorded(scenario, "guard.envelope_refused")
    await transport.close()
    await scenario.listener.stop()


async def test_a_forged_segment_gets_its_cell_isolated_by_rule_through_the_queens_door() -> None:
    scenario = await build_scenario()
    scenario.listener.bind_trail_receiver(TrailSegmentReceiver(scenario.trail))
    cell, transport = await attach(scenario)
    run = asyncio.ensure_future(scenario.queen.run())
    guard_bee = guard_bee_for_queen(scenario.deps, scenario.queen)  # Her own door, not a stub.
    clock = FakeClock()
    source_trail = MemoryPheromoneTrail(clock)
    await source_trail.record(_provisioned(clock, cell, scenario.hive_id))
    chunk = await _one_trail_segment_sync_chunk(clock, cell, source_trail, scenario.hive_id)
    forged = chunk.model_copy(update={"node_id": new_node_id(clock)})

    await transport.send(wrap(forged, cell.hop(scenario.hive_id), clock=clock))
    await asyncio.wait_for(until_async(lambda: recorded(scenario, "guard.segment_refused")), WAIT_S)
    [response] = await guard_bee.tick()
    await asyncio.wait_for(until_async(lambda: recorded(scenario, "cell.isolated")), WAIT_S)

    report = response.report
    assert (report.rule, report.cell_id, response.disposition) == (
        "segment_forgery",
        cell.cell_id,
        Disposition.FILED,
    )
    [decided] = await scenario.trail.query(TrailQuery(kind="queen.decided"))
    assert (decided.payload["basis"], decided.payload["report_id"]) == ("rule", report.id)
    [isolated] = await scenario.trail.query(TrailQuery(kind="cell.isolated"))
    assert (isolated.subject_id, isolated.payload["report_id"]) == (cell.cell_id, report.id)
    await guard_bee.aclose()
    await scenario.queen.stop()
    await asyncio.wait_for(run, WAIT_S)
    await transport.close()
    await scenario.listener.stop()


def _provisioned(clock: FakeClock, cell: FakeCell, hive_id: HiveId) -> CellEvent:
    """One `cell.provisioned` recorded on `cell`'s own node, for its segment to carry."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=hive_id,
        node_id=cell.node_id,
        at=clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=cell.cell_id,
        payload={},
    )


async def test_a_replayed_envelope_id_is_dropped_and_recorded_with_the_nodes_id() -> None:
    scenario = await build_scenario()
    cell, transport = await attach(scenario)
    beat = CellHeartbeat(
        cell_id=cell.cell_id,
        mode=CellMode.ACTIVE,
        lease_ids=(),
        worker_count=0,
        is_shield_verified=True,
        interval_s=15.0,
    )
    envelope = wrap(beat, cell.hop(scenario.hive_id), clock=FakeClock())

    await transport.send(envelope)
    await transport.send(envelope)  # The same envelope id again: a replay, not a second beat.

    async def _recorded() -> bool:
        return bool(await scenario.trail.query(TrailQuery(kind="guard.replay_refused")))

    await asyncio.wait_for(until_async(_recorded), WAIT_S)
    [refused] = await scenario.trail.query(TrailQuery(kind="guard.replay_refused"))
    assert refused.subject_id == cell.node_id
    assert refused.payload == {"node_id": cell.node_id, "envelope_id": envelope.id}
    assert cell.warden_id in attached_ids(scenario.queen)  # A replay costs the Cell nothing more.
    await transport.close()
    await scenario.listener.stop()


async def test_a_frame_flood_closes_the_link_and_is_recorded_with_the_nodes_id() -> None:
    scenario = await build_scenario()
    cell, transport = await attach(scenario)
    # A snapshot request is answered inline by the listener's pump, with no handler bound here
    # dropped there: unlike a Heartbeat it never waits in the Queen's queue, so nothing slows the
    # flood by backpressure and the ceiling is what ends it.
    request = CellSnapshotRequest(cell_id=cell.cell_id, purpose="flood")

    # The listener's clock never advances here, so the bucket never refills: the burst allowance
    # is all the connection gets, and the two handshake frames already spent two of it. Exactly a
    # burst's worth more crosses the line by one frame and no further, so the close handshake the
    # listener starts then is answered at once instead of waiting behind a long unread flood.
    for _ in range(CEILING_BURST_FRAMES):
        await transport.send(wrap(request, cell.hop(scenario.hive_id), clock=FakeClock()))

    await asyncio.wait_for(
        until(lambda: cell.warden_id not in attached_ids(scenario.queen)), WAIT_S
    )
    [refused] = await scenario.trail.query(TrailQuery(kind="guard.frame_ceiling"))
    assert refused.subject_id == cell.node_id
    assert refused.payload["node_id"] == cell.node_id
    await scenario.listener.stop()
