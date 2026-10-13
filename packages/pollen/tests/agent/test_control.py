"""Test the housekeeping messages end to end: node updates and the Queen's relocation notice.

Two messages change what the Pollen Packet (the thin gateway program on a device the Hive borrows)
may do or whom it trusts, and both come only from the Queen (the Hive's central orchestrator). A
``swarm.node_update`` replaces the node's standing terms and is persisted before it applies from
the *next* request, a request inside a lease opened before it included; it never changes the
runtime level, which only colonization could. A ``control.queen_moved`` announces that the Hive
Stand is moving: the packet pins the new node's key, dials the new address from ``effective_at``,
accepts either node's frames until ``grace_until``, and then forgets the old node entirely; a
notice whose sequence is not above the highest accepted is ignored, so a replay can never
redirect it. These tests run both through a real packet, with a second scripted Queen standing in
for the machine the Hive moves to.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.control and, through it,
    pollen.agent.moved and the sentinel's follow-the-Queen duty.

Key invariants:
    - What a test asserts about persistence it reads back from the data directory, not from the
      packet's memory.
    - Every packet and both Queens are stopped before a test ends.

See Also:
    - docs/waggle/enrolment.md sections 10 and 12.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from device_kit import Device, Terms, make_device, running
from queen_double import QueenDouble
from scenarios import eventually, exec_message, open_lease, python_argv

from pollen.state import load_state
from waggle.clock import Clock
from waggle.messages.control import ErrorMessage, QueenMoved
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.session import SessionExit
from waggle.messages.swarm import DeviceHeartbeat, NodeUpdate, RuntimeLevel


def _update(device: Device, queen: QueenDouble, **fields: object) -> NodeUpdate:
    """Build a node update that changes nothing unless a field is overridden."""
    values: dict[str, object] = {
        "device_id": device.state.device_id,
        "cell_id": device.state.cell_id,
        "warden_id": queen.warden_id,
        "level": RuntimeLevel.GATEWAY,
        "access_level": device.state.access_level,
        "comb_shield": CombShieldLevel.MEADOW,
        "granted_capabilities": device.state.granted_capabilities,
        "offline_limit_s": device.state.offline_limit_s,
        "heartbeat_interval_s": device.state.heartbeat_interval_s,
        "reason": "The operator changed the node.",
    }
    values.update(fields)
    return NodeUpdate(**values)


async def test_a_node_update_applies_from_the_next_request_even_inside_an_open_lease(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    terms = Terms(
        access=AccessLevel.READ_ONLY, capabilities=["fs:read:**", f"watch:{queen.cell_id}"]
    )
    device = make_device(queen, tmp_path / "data", clock, terms)
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session, access=AccessLevel.READ_ONLY)
        denied = await session.send(
            exec_message(lease.lease_id, python_argv("print(1)")), sender=queen.warden_id
        )
        refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == denied.id)
        assert refused.payload.code == "pollen.capability_denied"
        grants = (*device.state.granted_capabilities, "exec:*")
        await session.send(
            _update(device, queen, granted_capabilities=grants, heartbeat_interval_s=9.0)
        )
        await eventually(lambda: "exec:*" in load_state(device.layout.state).granted_capabilities)
        saved = load_state(device.layout.state)
        assert saved.heartbeat_interval_s == 9.0
        allowed = await session.send(
            exec_message(lease.lease_id, python_argv("print(1)")), sender=queen.warden_id
        )
        ended = await session.expect(SessionExit, where=lambda e: e.correlation_id == allowed.id)
        assert ended.payload.exit_code == 0


async def test_a_node_update_cannot_move_the_runtime_level(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        await session.send(
            _update(device, queen, level=RuntimeLevel.COLONIZED, offline_limit_s=77.0)
        )
        await eventually(lambda: load_state(device.layout.state).offline_limit_s == 77.0)
        assert load_state(device.layout.state).level is RuntimeLevel.GATEWAY
        beat = await session.expect(DeviceHeartbeat)
        assert beat.payload.level is RuntimeLevel.GATEWAY


async def test_a_node_update_for_another_device_is_refused_and_changes_nothing(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        other = device.state.device_id[:-1] + ("A" if device.state.device_id[-1] != "A" else "B")
        sent = await session.send(_update(device, queen, device_id=other, offline_limit_s=5.0))
        refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
        assert refused.payload.code == "waggle.codec.invalid_payload"
        assert load_state(device.layout.state).offline_limit_s == device.state.offline_limit_s


async def test_a_node_update_from_the_warden_is_ignored(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        await session.send(_update(device, queen, offline_limit_s=5.0), sender=queen.warden_id)
        await session.expect_none(ErrorMessage)
        assert load_state(device.layout.state).offline_limit_s == device.state.offline_limit_s


async def _new_home(queen: QueenDouble, device: Device, clock: Clock) -> QueenDouble:
    """Start the machine the Hive moves to: the same Hive, a new node, a new address."""
    moved = QueenDouble(clock)
    moved.hive_id, moved.warden_id, moved.cell_id = queen.hive_id, queen.warden_id, queen.cell_id
    await moved.start()
    moved.register(device.state.node_id, device.signer.public_key_bytes)
    return moved


async def test_a_relocation_redirects_the_packet_and_forgets_the_old_node_after_the_grace(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    new_queen = await _new_home(queen, device, clock)
    try:
        async with running(device, clock):
            session = await queen.next_session()
            now = clock.now()
            notice = QueenMoved(
                sequence=1,
                new_address=new_queen.url,
                new_node_id=new_queen.hive_node_id,
                new_node_public_key_hex=new_queen.hive_key_hex,
                effective_at=now + timedelta(seconds=0.3),
                grace_until=now + timedelta(seconds=1.5),
                reason="The Hive Stand is moving.",
            )
            await session.send(notice)
            await eventually(lambda: load_state(device.layout.state).moved_sequence == 1)
            moved = await new_queen.next_session()
            assert isinstance(moved.frames[0].payload, DeviceHeartbeat)
            assert moved.frames[0].recipient == queen.hive_id
            await eventually(lambda: load_state(device.layout.state).queen_move is None)
            settled = load_state(device.layout.state)
            assert settled.queen_url == new_queen.url
            assert settled.hive_node_id == new_queen.hive_node_id
            assert settled.hive_key_hex == new_queen.hive_key_hex
            assert queen.hive_node_id not in {n.node_id for n in settled.trusted_nodes}
    finally:
        await new_queen.stop()


async def test_an_old_or_replayed_notice_never_redirects_the_packet(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    new_queen = await _new_home(queen, device, clock)
    try:
        async with running(device, clock):
            session = await queen.next_session()
            now = clock.now()

            def notice(sequence: int, address: str) -> QueenMoved:
                return QueenMoved(
                    sequence=sequence,
                    new_address=address,
                    new_node_id=new_queen.hive_node_id,
                    new_node_public_key_hex=new_queen.hive_key_hex,
                    effective_at=now + timedelta(days=1),
                    grace_until=now + timedelta(days=2),
                    reason="A move.",
                )

            await session.send(notice(5, new_queen.url))
            await eventually(lambda: load_state(device.layout.state).moved_sequence == 5)
            await session.send(notice(5, "ws://127.0.0.1:1"))
            await session.send(notice(3, "ws://127.0.0.1:2"))
            await session.expect_none(ErrorMessage)
            kept = load_state(device.layout.state)
            assert kept.moved_sequence == 5
            assert kept.queen_move is not None and kept.queen_move.new_url == new_queen.url
    finally:
        await new_queen.stop()
