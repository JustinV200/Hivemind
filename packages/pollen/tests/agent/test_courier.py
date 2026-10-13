"""Test how the packet sends: live frames, durable frames through the outbox, and the replay.

Everything the Pollen Packet (the thin gateway program on a device the Hive borrows) says goes
through its courier. A live frame (an output chunk) is sent now or dropped, because the Warden (the
per-Cell supervisor) that lost the link has given up on it; a durable frame (the release of a lease,
the exit of a program that outlived the link) is appended to the outbox and flushed to disk *before*
it is sent, acknowledged once sent, and replayed in order after the next reconnection, behind the
first heartbeat and the capacity report (gateway-contract sections 8 and 14). These tests drive a
real courier over a real link to the scripted Queen, then run a program that outlives its link
through a whole packet.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.courier and pollen.agent.link.

Key invariants:
    - A durable frame is on disk before it is sent, and a frame that was sent is never sent again.
    - An output chunk is never queued.

See Also:
    - docs/waggle/gateway-contract.md sections 8 and 14.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from device_kit import Device, live_lease, running
from queen_double import QueenDouble, QueenSession
from scenarios import eventually, exec_message, python_argv
from states import frame_from

from pollen.agent import Courier, Identity, Link, LiveVerifier
from pollen.errors import NotFoundError
from pollen.lease import LeaseTable
from waggle.clock import Clock, FakeClock
from waggle.ids import LeaseId
from waggle.messages.cell import LeaseReleased, ReleaseCause
from waggle.messages.control import ErrorMessage, Ping
from waggle.messages.forage import CapacityReport
from waggle.messages.session import (
    SessionClose,
    SessionExit,
    SessionOutcome,
    SessionOutput,
    SessionPutFile,
)
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel
from waggle.outbox import Outbox
from waggle.signing import public_key_from_hex


class Wire:
    """A courier, a link to the scripted Queen and the Queen's side of that link."""

    def __init__(self, courier: Courier, link: Link, session: QueenSession) -> None:
        """Hold the three halves a test works with."""
        self.courier, self.link, self.session = courier, link, session


@pytest.fixture
async def wire(
    queen: QueenDouble, device: Device, clock: Clock, tmp_path: Path
) -> AsyncIterator[Wire]:
    keys: dict[str, bytes] = {queen.hive_node_id: public_key_from_hex(queen.hive_key_hex)}
    link = Link(queen.url, None, device.signer, LiveVerifier(keys), clock)
    await link.connect()
    state = device.state
    courier = Courier(
        Identity(state.device_id, state.node_id, state.hive_id),
        clock,
        Outbox(tmp_path / "ob.jsonl"),
    )
    # The Queen's side exists once a first frame arrives: send a heartbeat as a packet does.
    await link.send(courier.envelope(_beat(state.device_id), to=state.hive_id))
    session = await queen.next_session()
    try:
        yield Wire(courier, link, session)
    finally:
        await link.close()


def _beat(device_id: str) -> DeviceHeartbeat:
    """A heartbeat to open the Queen's side of a connection."""
    return DeviceHeartbeat(
        device_id=device_id,
        level=RuntimeLevel.GATEWAY,
        lease_ids=(),
        hive_started_processes=0,
        outbox_pending=0,
        uptime_s=0.0,
    )


async def test_a_live_frame_with_no_link_is_dropped_not_queued(wire: Wire, device: Device) -> None:
    hive = device.state.hive_id
    assert await wire.courier.send(Ping(), to=hive) is False
    assert wire.courier.pending == 0


async def test_a_durable_frame_with_no_link_waits_and_is_replayed_once_in_order(
    wire: Wire, device: Device
) -> None:
    hive = device.state.hive_id
    lease = "lease_01M4TJ4GG08H248H248H248H24"
    for reason in ("first", "second", "third"):
        await wire.courier.send_durable(SessionClose(lease_id=lease, reason=reason), to=hive)
    assert wire.courier.pending == 3
    report = await wire.courier.flush(wire.link)
    assert (report.sent, report.stopped_by) == (3, None)
    for reason in ("first", "second", "third"):
        sent = await wire.session.expect(SessionClose)
        assert sent.payload.reason == reason
    assert wire.courier.pending == 0
    assert (await wire.courier.flush(wire.link)).sent == 0


async def test_a_durable_frame_with_a_link_is_sent_and_acknowledged_at_once(
    wire: Wire, device: Device
) -> None:
    wire.courier.attach(wire.link)
    await wire.courier.send_durable(Ping(), to=device.state.hive_id)
    await wire.session.expect(Ping)
    assert wire.courier.pending == 0


async def test_a_frame_that_fails_to_send_stays_pending(wire: Wire, device: Device) -> None:
    wire.courier.attach(wire.link)
    await wire.link.close()
    assert await wire.courier.send(Ping(), to=device.state.hive_id) is False
    await wire.courier.send_durable(Ping(), to=device.state.hive_id)
    assert wire.courier.pending == 1
    assert not wire.courier.is_attached


async def test_a_frame_older_than_a_week_is_dropped_before_the_replay(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    clock = FakeClock()
    state = device.state
    courier = Courier(
        Identity(state.device_id, state.node_id, state.hive_id),
        clock,
        Outbox(tmp_path / "old.jsonl"),
    )
    await courier.send_durable(Ping(), to=state.hive_id)
    clock.advance(timedelta(days=8).total_seconds())
    keys: dict[str, bytes] = {queen.hive_node_id: public_key_from_hex(queen.hive_key_hex)}
    link = Link(queen.url, None, device.signer, LiveVerifier(keys), FakeClock())
    await link.connect()
    try:
        assert (await courier.flush(link)).sent == 0
        assert courier.pending == 0
    finally:
        await link.close()


async def test_a_failure_answers_the_request_with_its_kind_and_code(
    wire: Wire, device: Device, clock: Clock
) -> None:
    wire.courier.attach(wire.link)
    request = frame_from(device.state, Ping(), clock, sender=device.state.warden_id)
    await wire.courier.fail(request, NotFoundError("Nothing is there."))
    error = await wire.session.expect(ErrorMessage)
    # The answer goes to whoever asked: here the Warden.
    assert error.correlation_id == request.id and error.recipient == device.state.warden_id
    assert (error.payload.code, error.payload.failed_kind, error.payload.is_retryable) == (
        "pollen.not_found",
        "control.ping",
        False,
    )


async def test_a_failed_put_file_chunk_answers_the_transfer_not_the_chunk(
    wire: Wire, device: Device, clock: Clock
) -> None:
    wire.courier.attach(wire.link)
    transfer = "msg_01M4TJ4GG0ANANANANANANANAN"
    chunk = SessionPutFile(
        lease_id="lease_01M4TJ4GG08H248H248H248H24",
        transfer_id=transfer,
        total_bytes=4,
        path="x",
        chunk=b"ab",
        offset=0,
        sha256=None,
        task_id=None,
        worker_id=None,
    )
    request = frame_from(device.state, chunk, clock)
    await wire.courier.fail(request, NotFoundError("Nothing is there."))
    error = await wire.session.expect(ErrorMessage)
    assert error.correlation_id == transfer != request.id


async def test_a_program_that_outlives_the_link_reports_its_exit_after_the_reconnection(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with live_lease(queen, device, clock) as live:
        code = "import time; print('up', flush=True); time.sleep(0.5); print('late', flush=True)"
        started = await live.session.send(
            exec_message(live.lease.lease_id, python_argv(code)), sender=queen.warden_id
        )
        await live.session.expect(SessionOutput, where=lambda e: e.correlation_id == started.id)
        port = queen.port
        await queen.stop()  # The link drops while the program is still running.
        await eventually(lambda: _tracked(device, live.lease.lease_id) == 0, wait_s=15)
        await queen.start(port)
        again = await queen.next_session()
        assert isinstance(again.frames[0].payload, DeviceHeartbeat)
        report = await again.expect(CapacityReport)
        ended = await again.expect(SessionExit, where=lambda e: e.correlation_id == started.id)
        assert (ended.payload.outcome, ended.payload.exit_code) == (SessionOutcome.EXITED, 0)
        assert again.frames.index(report.envelope) < again.frames.index(ended.envelope)
        # Output the dropped link could not take was discarded, not queued behind the exit.
        assert not [e for e in again.frames if isinstance(e.payload, SessionOutput)]


def _tracked(device: Device, lease_id: LeaseId) -> int:
    """Return how many processes the lease table tracks for ``lease_id`` (-1 if it is gone)."""
    held = LeaseTable.load(device.layout.lease_table).get(lease_id)
    return -1 if held is None else len(held.processes)


async def test_a_stopped_packet_leaves_its_reports_in_the_outbox_for_the_next_run(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with live_lease(queen, device, clock) as live:
        lease_id = live.lease.lease_id
    # Stopping released the lease (nothing outlives the packet); the report waited in the outbox.
    async with running(device, clock):
        session = await queen.next_session()
        released = await session.expect(LeaseReleased)
        assert released.payload.lease_id == lease_id
        assert released.payload.cause is ReleaseCause.ORPHAN_SWEEP
