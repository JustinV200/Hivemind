"""Test the packet's loop end to end against a scripted Queen over a real loopback WebSocket.

The Pollen Packet (the thin gateway program on a device the Hive borrows) has one loop: dial the
Queen (the Hive's central orchestrator), send a signed heartbeat as the first frame, then a
capacity report, replay the outbox, serve, and dial again when the link ends. These tests drive
that loop with the scripted Queen of ``support/queen_double.py`` and check the contract's
promises that only a whole packet can keep: the order of the first frames, the heartbeat that
keeps coming, the reconnection after a dropped link, the refusal of a tampered or replayed frame,
and the stop that a protocol mismatch forces.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.packet and, through it, the
    dispatcher, the pulse and the link; depends on pollen, waggle and the support helpers.

Key invariants:
    - Each test waits for the frame it needs with a bounded ``expect``; none sleeps a fixed time to
      "let something happen", except to prove a frame does *not* arrive.
    - Every packet started here is stopped and awaited before the test ends.

See Also:
    - docs/waggle/gateway-contract.md sections 5, 6 and 7.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from device_kit import FAST_TIMINGS, Device, running
from fast_clock import FastClock
from queen_double import QueenDouble

from pollen import build_packet
from pollen.agent import Link, Pulse
from pollen.agent.packet import failure_code
from pollen.errors import PathRefusedError, ProtocolMismatchError
from pollen.platform import select_shim
from waggle.clock import Clock
from waggle.loop import INITIAL_BACKOFF_S
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.control import ErrorMessage, Ping, Pong, Wake
from waggle.messages.forage import CapacityReport, CapacityTrigger
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel


async def test_the_first_frame_is_a_signed_heartbeat_then_a_reconnected_capacity_report(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        first = session.frames[0]
        assert isinstance(first.payload, DeviceHeartbeat)
        assert (first.sender, first.recipient) == (device.state.device_id, queen.hive_id)
        assert first.node_id == device.state.node_id
        assert first.payload.level is RuntimeLevel.GATEWAY
        report = await session.expect(CapacityReport)
        assert report.payload.trigger is CapacityTrigger.RECONNECTED
        assert session.frames[1] is report.envelope


async def test_heartbeats_keep_coming_at_the_interval(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        await session.expect(DeviceHeartbeat)  # The first frame.
        later = await session.expect(DeviceHeartbeat)
        assert later.payload.outbox_pending == 0
        assert later.payload.lease_ids == ()


async def test_the_packet_dials_again_when_the_queen_drops_the_link(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        first = await queen.next_session()
        await first.close(1000)
        second = await queen.next_session()
        assert second is not first
        assert isinstance(second.frames[0].payload, DeviceHeartbeat)


async def test_a_ping_is_answered_with_a_pong_that_carries_the_protocol_version(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        ping = await session.send(Ping())
        pong = await session.expect(Pong)
        assert pong.correlation_id == ping.id
        assert pong.version.startswith("1.")


async def test_a_tampered_frame_closes_the_link_and_the_packet_reconnects(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        sent = queen.encode(queen.build(Ping(), device.state.device_id))
        tampered = sent.replace(b'"control.ping"', b'"control.pong"')
        await session.send_frame(tampered)
        again = await queen.next_session()
        assert again is not session
        assert session.close_code is not None or session.is_closed


async def test_a_replayed_frame_is_acted_on_once(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        envelope = queen.build(Ping(), device.state.device_id)
        raw = queen.encode(envelope)
        await session.send_frame(raw)
        await session.expect(Pong)
        await session.send_frame(raw)
        await session.expect_none(Pong)


async def test_a_protocol_major_mismatch_stops_the_packet(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    packet = build_packet(device.layout, select_shim(), clock)
    task = asyncio.create_task(packet.run())
    session = await queen.next_session()
    await session.close(1002, "waggle.version.unsupported_major")
    with pytest.raises(ProtocolMismatchError):
        await asyncio.wait_for(task, timeout=10)


async def test_a_signed_frame_with_a_bad_payload_is_answered_and_the_link_stays_open(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        # Built without validation, so it is signed as it is: the packet is the first to object.
        broken = Wake.model_construct(provider=None, reason="x" * (MAX_REASON_CHARS + 1))
        sent = queen.build(broken, device.state.device_id)
        await session.send_frame(queen.encode(sent))
        answer = await session.expect(ErrorMessage)
        assert answer.payload.code == "waggle.codec.invalid_payload"
        assert answer.correlation_id == sent.id
        await session.send(Ping())
        await session.expect(Pong)  # The same connection still serves.
        assert not session.is_closed


async def test_a_bug_while_connecting_is_contained_logged_by_type_and_retried(
    queen: QueenDouble,
    device: Device,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="pollen.agent.packet")
    real = Pulse.announce
    attempts: list[int] = []

    async def flaky(pulse: Pulse, link: Link) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("secret detail about /etc/shadow")
        await real(pulse, link)

    monkeypatch.setattr(Pulse, "announce", flaky)
    async with running(device, clock):
        session = await queen.next_session()  # The second attempt, after the bug.
        assert isinstance(session.frames[0].payload, DeviceHeartbeat)
    assert "pollen.connection_bug error=RuntimeError" in caplog.text
    assert "secret" not in caplog.text and "shadow" not in caplog.text


class _ParkedClock(FastClock):
    """A fast clock whose reconnection backoffs really wait, and says when one begins."""

    def __init__(self) -> None:
        """Start with no backoff begun."""
        super().__init__()
        self.backing_off = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        """Wait briefly as the fast clock does, except for a backoff, which waits for an hour."""
        if seconds < INITIAL_BACKOFF_S:
            await super().sleep(seconds)
            return
        self.backing_off.set()
        await asyncio.sleep(3600)  # Only a stop that cuts the wait short gets past this.


async def test_a_stop_during_the_reconnection_backoff_is_heard_at_once(
    queen: QueenDouble, device: Device
) -> None:
    clock = _ParkedClock()
    packet = build_packet(device.layout, select_shim(), clock, FAST_TIMINGS)
    task = asyncio.create_task(packet.run())
    try:
        await queen.next_session()
        await queen.stop()  # The link drops and every later dial is refused: the backoff begins.
        async with asyncio.timeout(10):
            await clock.backing_off.wait()
        packet.stop()
        # A service manager gives seconds, and a stop that waited out the backoff would be killed
        # before the shutdown sweep released the leases.
        await asyncio.wait_for(task, timeout=5)
    finally:
        task.cancel()  # A no-op once the packet has stopped, as it should have.
        await asyncio.gather(task, return_exceptions=True)


def test_a_failure_is_named_by_its_own_code_or_by_its_kind_never_by_its_text() -> None:
    assert failure_code(PathRefusedError("a private path")) == "pollen.path_refused"
    assert failure_code(TimeoutError("10.0.0.1")) == "pollen.link.timeout"
    assert failure_code(OSError("10.0.0.1")) == "pollen.link.os_error"
