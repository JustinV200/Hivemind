"""Test the packet's clocks end to end: capacity reports, the dead-man switch, persistence.

Two duties of the Pollen Packet (the thin gateway program on a device the Hive borrows) happen on
a timer and not because a frame arrived. It sends capacity reports when a figure moves or on a slow
timer, and it watches the Queen (the Hive's central orchestrator): if she has not been heard from
for longer than the enrolled limit, the packet has no Warden (the per-Cell supervisor) on the
device to ask, so it releases every lease itself and keeps dialling (gateway-contract section 13).
The switch must not trip on a quiet but healthy link (a pong counts as hearing from her), must
trip when the Queen keeps closing the link at once (a revoked node), and must be reported to the
Queen once she is back. These tests run a real packet with intervals shrunk to a fraction of a
second and read what the scripted Queen sees and what the data directory holds.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.heartbeat's pulse and sentinel
    through a running packet.

Key invariants:
    - Time is real here (the switch is measured on the monotonic clock); only the limits are small.
    - A test that stops the Queen restarts it on the same port, so the packet's reconnection is
      what is under test.

See Also:
    - docs/waggle/gateway-contract.md sections 8, 13 and 14.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from device_kit import Device, Terms, live_lease, make_device, running
from disk import is_dir
from queen_double import QueenDouble
from scenarios import eventually, open_lease

from pollen.agent import read_status
from pollen.agent.status import StatusDocument
from pollen.lease import LeaseTable
from pollen.state import load_state
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.messages.cell import LeaseReleased, ReleaseCause
from waggle.messages.forage import CapacityReport, CapacityTrigger
from waggle.messages.reports import HostCapacityReport
from waggle.messages.swarm import DeviceHeartbeat


async def test_a_capacity_report_follows_on_a_timer(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        first = await session.expect(CapacityReport)
        assert first.payload.trigger is CapacityTrigger.RECONNECTED
        later = await session.expect(CapacityReport)
        assert later.payload.trigger in {CapacityTrigger.PERIODIC, CapacityTrigger.CHANGED}
        assert later.payload.model_servers == ()


async def test_the_dead_man_switch_releases_the_leases_and_reports_when_the_queen_returns(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(offline_limit_s=0.6))
    async with live_lease(queen, device, clock) as live:
        scratch = Path(live.lease.scratch_root)
        port = queen.port
        await queen.stop()
        await eventually(lambda: not scratch.exists(), wait_s=15)
        await eventually(lambda: LeaseTable.load(device.layout.lease_table).all() == (), wait_s=15)
        await queen.start(port)
        session = await queen.next_session()
        first = session.frames[0]
        assert isinstance(first.payload, DeviceHeartbeat) and first.payload.lease_ids == ()
        report = await session.expect(CapacityReport)
        released = await session.expect(LeaseReleased)
        assert released.payload.cause is ReleaseCause.DEAD_MAN
        assert (released.payload.lease_id, released.payload.is_restored) == (
            live.lease.lease_id,
            True,
        )
        # Replayed from the outbox, behind the heartbeat and the capacity report.
        assert session.frames.index(report.envelope) < session.frames.index(released.envelope)


async def test_a_quiet_but_healthy_link_does_not_trip_the_switch(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(offline_limit_s=0.5))
    async with live_lease(queen, device, clock) as live:
        await live.session.expect_none(LeaseReleased, within=1.5)
        assert is_dir(Path(live.lease.scratch_root))
        held = LeaseTable.load(device.layout.lease_table)
        assert held.get(live.lease.lease_id) is not None


async def test_a_queen_that_closes_every_link_at_once_cannot_keep_the_switch_from_tripping(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(offline_limit_s=0.6))
    async with live_lease(queen, device, clock) as live:
        scratch = Path(live.lease.scratch_root)
        queen.is_revoking = True  # As for a revoked node: every dial is closed at its first frame.
        await live.session.close(1008)
        await eventually(lambda: not scratch.exists(), wait_s=15)
        queen.is_revoking = False
        # The sessions dialed while revoking were refused at their first frame, and each closes a
        # moment after it, so one can still look open with a frame; wait for one that was let in.
        await eventually(
            lambda: (
                bool(queen.sessions[-1].frames)
                and queen.sessions[-1].refusal is None
                and not queen.sessions[-1].is_closed
            )
        )
        released = await queen.sessions[-1].expect(LeaseReleased)
        assert released.payload.cause is ReleaseCause.DEAD_MAN


async def test_last_heard_is_persisted_coarsely_and_the_status_document_is_written(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    assert device.state.last_heard_at is None
    async with running(device, clock):
        session = await queen.next_session()
        await open_lease(queen, session)
        await eventually(lambda: load_state(device.layout.state).last_heard_at is not None)
        await eventually(lambda: _open_leases(device) == 1)
        status = read_status(device.layout.status)
        assert status is not None
        assert (status.is_connected, status.outbox_pending) == (True, 0)
        assert status.last_failure is None and not status.is_clock_suspect


def _open_leases(device: Device) -> int:
    """Return the lease count in the status document, or -1 while there is none yet."""
    status = read_status(device.layout.status)
    return -1 if status is None else status.open_leases


def _is_changed(envelope: Envelope) -> bool:
    """Say whether ``envelope`` is a capacity report sent because a figure moved."""
    payload = envelope.payload
    return isinstance(payload, CapacityReport) and payload.trigger is CapacityTrigger.CHANGED


def _host(free: int) -> HostCapacityReport:
    """A host snapshot with round figures and ``free`` bytes of memory free of 1000."""
    return HostCapacityReport(
        cores=4,
        memory_bytes=1000,
        memory_free_bytes=free,
        disk_bytes=10_000,
        disk_free_bytes=5_000,
        cpu_load=0.1,
        gpus=(),
    )


async def test_a_material_change_in_the_host_is_reported_at_once_as_changed(
    queen: QueenDouble, device: Device, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    readings = [_host(900)]  # What the announcement sees; every later look sees 100 free.

    def look(_root: Path) -> HostCapacityReport:
        return readings.pop(0) if readings else _host(100)

    monkeypatch.setattr("pollen.agent.heartbeat.host_capacity", look)
    async with running(device, clock):
        session = await queen.next_session()
        first = await session.expect(CapacityReport)
        assert first.payload.trigger is CapacityTrigger.RECONNECTED
        changed = await session.expect(CapacityReport, where=_is_changed)
        assert changed.payload.host.memory_free_bytes == 100


async def test_a_status_document_that_cannot_be_written_is_logged_and_not_fatal(
    queen: QueenDouble,
    device: Device,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="pollen.agent.heartbeat")

    async def refuse(_path: Path, _document: StatusDocument) -> None:
        raise OSError("disk full at /somewhere/private")

    monkeypatch.setattr("pollen.agent.heartbeat.write_status", refuse)
    async with running(device, clock):
        session = await queen.next_session()
        await session.expect(DeviceHeartbeat)  # The first frame.
        await session.expect(DeviceHeartbeat)  # And the packet is still beating.
    assert "pollen.status_not_written" in caplog.text and "private" not in caplog.text
