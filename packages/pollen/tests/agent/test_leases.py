"""Test device leases end to end: open, release, close, and the sweep a restart performs.

A lease is the Hive's temporary hold on a borrowed device (gateway-contract section 9.1), and the
Pollen Packet (the thin gateway program on the device) is the only one who can open or end it
there. These tests run a real packet against the scripted Queen and check what the contract
promises: a scratch directory for the lease alone, written into the persisted table before the
answer; the lower of the access levels asked for and held; a refusal for another Cell or holder or
for a whole-Cell retirement; a release that ends every program, puts back every change outside
scratch and removes scratch, reporting how it went; a session close that ends programs and leaves
the lease; and the sweep at startup that releases leases a crash left in the table.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.leases through a running
    packet, with the executors and the lease table behind it.

Key invariants:
    - Every assertion is about what the Queen sees on the wire or what is on the disk afterwards.
    - A test that needs a program uses this interpreter, which every host has.

See Also:
    - docs/waggle/gateway-contract.md sections 9.1, 12 and 13.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import pytest
from device_kit import Device, Terms, make_device, running
from disk import exists, is_dir, permission_bits
from queen_double import QueenDouble, QueenSession
from scenarios import eventually, exec_message, open_lease, put_file, python_argv, teardown

from pollen.lease import DeviceLease, LeaseState, LeaseTable, ProcessKind, ProcessRecord
from pollen.lease import restore as restore_module
from pollen.platform import select_shim
from pollen.platform.base import SpawnSpec
from pollen.state import DataLayout
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.ids import new_cell_id, new_lease_id
from waggle.messages.cell import CellRequest, CellTeardownRequest, LeaseReleased, ReleaseCause
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel, Urgency
from waggle.messages.session import SessionClose, SessionExit, SessionOutcome, SessionOutput
from waggle.messages.swarm import DeviceHeartbeat

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def _lists(envelope: Envelope, lease_id: str) -> bool:
    """Say whether ``envelope`` is a heartbeat that names ``lease_id``."""
    return isinstance(envelope.payload, DeviceHeartbeat) and lease_id in envelope.payload.lease_ids


@posix_only
async def test_opening_a_lease_makes_private_scratch_and_records_it_first(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session, quota=1_000_000)
        scratch = Path(lease.scratch_root)
        assert is_dir(scratch)
        assert scratch.parent.resolve() == (device.layout.root / "leases").resolve()
        assert permission_bits(scratch) == 0o700
        stored = LeaseTable.load(device.layout.lease_table).get(lease.lease_id)
        assert stored is not None
        assert (stored.quota_bytes, stored.state) == (1_000_000, LeaseState.OPEN)
        assert (lease.holder, lease.dead_man_s) == (queen.warden_id, device.state.offline_limit_s)
        assert lease.allowed_paths == ("**",)
        beat = await session.expect(DeviceHeartbeat, where=lambda e: _lists(e, lease.lease_id))
        assert beat.payload.lease_ids == (lease.lease_id,)


@pytest.mark.parametrize(
    "levels",  # What the device holds, what the lease asks for, and what it gets.
    [
        (AccessLevel.FULL, AccessLevel.READ_ONLY, AccessLevel.READ_ONLY),
        (AccessLevel.SCRATCH, AccessLevel.FULL, AccessLevel.SCRATCH),
    ],
)
async def test_a_lease_gets_the_lower_of_the_access_levels(
    queen: QueenDouble,
    tmp_path: Path,
    clock: Clock,
    levels: tuple[AccessLevel, AccessLevel, AccessLevel],
) -> None:
    held, asked, granted = levels
    device = make_device(queen, tmp_path / "data", clock, Terms(access=held))
    async with running(device, clock):
        lease = await open_lease(queen, await queen.next_session(), access=asked)
        assert lease.access_level is granted


async def test_a_request_for_another_cell_or_holder_is_refused(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        template = {
            "needs": None,
            "task_id": None,
            "access_level": AccessLevel.FULL,
            "lifetime_s": None,
            "reason": "x",
        }
        other_cell = CellRequest(
            cell_id=new_lease_id(clock).replace("lease_", "cell_"),
            holder=queen.warden_id,
            **template,
        )
        sent = await session.send(other_cell, sender=queen.warden_id)
        refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
        assert refused.payload.code == "pollen.lease_refused"
        stranger = CellRequest(
            cell_id=queen.cell_id,
            holder=queen.warden_id.replace("warden_", "warden_0")[:-1],
            **template,
        )
        sent = await session.send(stranger, sender=queen.warden_id)
        refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
        assert refused.payload.code == "pollen.lease_refused"


async def test_releasing_a_lease_removes_scratch_and_reports_it(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session)
        answer = await teardown(queen, session, lease.lease_id, cause=ReleaseCause.CANCELLED)
        released = answer.payload
        assert isinstance(released, LeaseReleased)
        assert (released.cause, released.is_restored, released.killed_processes) == (
            ReleaseCause.CANCELLED,
            True,
            0,
        )
        assert released.residual_paths == ()
        assert not exists(Path(lease.scratch_root))
        # The release is queued before the lease leaves the table, so the table empties just after.
        await eventually(lambda: LeaseTable.load(device.layout.lease_table).all() == ())


async def test_a_teardown_for_an_unknown_lease_or_a_whole_cell_is_refused(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        answer = await teardown(queen, session, new_lease_id(clock))
        assert isinstance(answer.payload, ErrorMessage)
        assert answer.payload.code == "hive.session.lease_not_open"
        # A teardown naming no lease asks to retire the whole Cell: a Real Cell never is.
        whole = CellTeardownRequest(
            cell_id=queen.cell_id,
            lease_id=None,
            urgency=Urgency.IMMEDIATE,
            cause=ReleaseCause.COMPLETED,
            reason="x",
        )
        sent = await session.send(whole, sender=queen.warden_id)
        refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
        assert refused.payload.code == "pollen.lease_refused"


@posix_only
async def test_releasing_ends_a_running_exec_and_counts_it(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session)
        slow = python_argv("import time; print('ready', flush=True); time.sleep(60)")
        started = await session.send(exec_message(lease.lease_id, slow), sender=queen.warden_id)
        # Wait until the program is really running: a release that overtakes the spawn kills
        # nothing that was alive, which is correct but not what this test is about.
        await session.expect(SessionOutput, where=lambda e: e.correlation_id == started.id)
        answer = await teardown(queen, session, lease.lease_id, urgency=Urgency.IMMEDIATE)
        assert isinstance(answer.payload, LeaseReleased)
        assert answer.payload.killed_processes == 1
        ended = await session.expect(SessionExit, where=lambda e: e.correlation_id == started.id)
        assert ended.payload.outcome is SessionOutcome.KILLED


@posix_only
async def test_releasing_puts_back_what_the_lease_changed_outside_scratch(
    queen: QueenDouble, device: Device, clock: Clock, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    kept = outside / "kept.txt"
    kept.write_bytes(b"original")
    created = outside / "created.txt"
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session)
        await put_file(queen, session, lease.lease_id, str(kept), b"changed")
        await put_file(queen, session, lease.lease_id, str(created), b"new")
        assert kept.read_bytes() == b"changed"
        released = (await teardown(queen, session, lease.lease_id)).payload
        assert isinstance(released, LeaseReleased)
        assert released.is_restored
        assert kept.read_bytes() == b"original"
        assert not created.exists()


async def test_closing_a_session_ends_its_programs_and_keeps_the_lease(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session)
        slow = exec_message(lease.lease_id, python_argv("import time; time.sleep(60)"))
        started = await session.send(slow, sender=queen.warden_id)
        close = SessionClose(lease_id=lease.lease_id, reason="The test is finished with it.")
        await session.send(close, sender=queen.warden_id)
        ended = await session.expect(SessionExit, where=lambda e: e.correlation_id == started.id)
        assert ended.payload.outcome is SessionOutcome.KILLED
        # Session traffic is refused from now on, but the lease itself is still held.
        again = await session.send(slow, sender=queen.warden_id)
        refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == again.id)
        assert refused.payload.code == "hive.session.lease_not_open"
        held = LeaseTable.load(device.layout.lease_table).get(lease.lease_id)
        assert held is not None and held.is_session_closed
        released = (await teardown(queen, session, lease.lease_id)).payload
        assert isinstance(released, LeaseReleased)


async def test_a_lease_left_in_the_table_is_swept_at_startup(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    scratch = device.layout.scratch(new_lease_id(clock))
    scratch.mkdir(parents=True)
    (scratch / "leftover.txt").write_text("x")
    lease = DeviceLease(
        lease_id=scratch.name,
        cell_id=device.state.cell_id,
        holder=device.state.warden_id,
        access_level=AccessLevel.FULL,
        scratch_root=str(scratch),
        quota_bytes=1000,
        opened_at=clock.now(),
    )
    table = LeaseTable(device.layout.lease_table)
    await table.put(lease)
    async with running(device, clock):
        session = await queen.next_session()
        swept = await session.expect(LeaseReleased)
        assert swept.payload.cause is ReleaseCause.ORPHAN_SWEEP
        assert swept.payload.is_restored
        assert not scratch.exists()


def _is_clean(envelope: Envelope) -> bool:
    """Say whether ``envelope`` reports a lease released with nothing left behind."""
    return isinstance(envelope.payload, LeaseReleased) and envelope.payload.is_restored


def _cell_request(queen: QueenDouble) -> CellRequest:
    """A well-formed request for a lease on the Queen's Cell, from its Warden."""
    return CellRequest(
        cell_id=queen.cell_id,
        needs=None,
        holder=queen.warden_id,
        task_id=None,
        access_level=AccessLevel.FULL,
        lifetime_s=None,
        scratch_quota_bytes=None,
        reason="A test needs the device.",
    )


async def _refusal(queen: QueenDouble, session: QueenSession, request: CellRequest) -> str:
    """Send a lease request as the Warden and return the code of the error that refused it."""
    sent = await session.send(request, sender=queen.warden_id)
    refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
    return refused.payload.code


async def test_a_lease_past_the_limit_is_refused(
    queen: QueenDouble, device: Device, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pollen.agent.leases.MAX_OPEN_LEASES", 1)
    async with running(device, clock):
        session = await queen.next_session()
        await open_lease(queen, session)
        assert await _refusal(queen, session, _cell_request(queen)) == "pollen.lease_refused"
        assert len(LeaseTable.load(device.layout.lease_table).all()) == 1


async def test_a_lease_whose_scratch_cannot_be_made_is_refused_and_not_recorded(
    queen: QueenDouble, device: Device, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = new_lease_id(clock)
    device.layout.scratch(taken).mkdir(parents=True)  # The directory is already there.
    monkeypatch.setattr("pollen.agent.leases.new_lease_id", lambda _clock: taken)
    async with running(device, clock):
        session = await queen.next_session()
        assert await _refusal(queen, session, _cell_request(queen)) == "pollen.lease_refused"
        assert LeaseTable.load(device.layout.lease_table).all() == ()


async def test_a_teardown_naming_another_cell_is_refused(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session)
        elsewhere = CellTeardownRequest(
            cell_id=new_cell_id(clock),
            lease_id=lease.lease_id,
            urgency=Urgency.IMMEDIATE,
            cause=ReleaseCause.COMPLETED,
            reason="A test names the wrong Cell.",
        )
        sent = await session.send(elsewhere, sender=queen.warden_id)
        refused = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
        assert refused.payload.code == "pollen.lease_refused"
        assert LeaseTable.load(device.layout.lease_table).get(lease.lease_id) is not None


@posix_only
async def test_the_startup_sweep_kills_a_process_an_earlier_run_recorded(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    shim = select_shim()
    leftover = await shim.spawn(
        SpawnSpec(
            argv=python_argv("import time; time.sleep(60)"),
            cwd=device.layout.root,
            env=dict(os.environ),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    )
    try:
        ref = shim.process_ref(leftover.pid)
        scratch = device.layout.scratch(new_lease_id(clock))
        scratch.mkdir(parents=True)
        held = DeviceLease(
            lease_id=scratch.name,
            cell_id=device.state.cell_id,
            holder=device.state.warden_id,
            access_level=AccessLevel.FULL,
            scratch_root=str(scratch),
            quota_bytes=1000,
            opened_at=clock.now(),
            processes=(ProcessRecord(pid=ref.pid, created=ref.created, kind=ProcessKind.EXEC),),
        )
        await LeaseTable(device.layout.lease_table).put(held)
        async with running(device, clock):
            session = await queen.next_session()
            swept = (await session.expect(LeaseReleased)).payload
            assert (swept.cause, swept.killed_processes) == (ReleaseCause.ORPHAN_SWEEP, 1)
            code = await asyncio.wait_for(leftover.wait(), timeout=10)
            # Always true here, since the test skips on Windows; it tells mypy, which checks the
            # module for Windows too, that SIGKILL is POSIX's.
            if sys.platform != "win32":
                assert code == -signal.SIGKILL
    finally:
        if leftover.returncode is None:
            leftover.kill()
            await leftover.wait()


async def test_a_release_that_cannot_remove_scratch_says_so_and_is_retried_until_clean(
    queen: QueenDouble, device: Device, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = restore_module.remove_scratch
    attempts: list[Path] = []

    def flaky(root: Path) -> bool:
        attempts.append(root)
        return False if len(attempts) == 1 else real(root)

    monkeypatch.setattr("pollen.agent.leases.remove_scratch", flaky)
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session)
        first = (await teardown(queen, session, lease.lease_id)).payload
        assert isinstance(first, LeaseReleased)
        assert not first.is_restored and first.residual_paths == (lease.scratch_root,)
        held = LeaseTable.load(device.layout.lease_table).get(lease.lease_id)
        assert held is not None and held.state is LeaseState.RELEASING  # Kept, to try again.
        clean = await session.expect(LeaseReleased, where=_is_clean)
        assert clean.payload.lease_id == lease.lease_id and clean.payload.residual_paths == ()
        await eventually(lambda: LeaseTable.load(device.layout.lease_table).all() == ())
        assert not exists(Path(lease.scratch_root))


async def test_a_release_that_fails_is_logged_by_type_and_retried(
    queen: QueenDouble,
    device: Device,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="pollen.agent.leases")
    real = restore_module.replay_restores
    failures: list[Exception] = [OSError("secret path /etc/shadow"), ValueError("secret bug")]

    def failing(layout: DataLayout, lease: DeviceLease) -> list[str]:
        if failures:
            raise failures.pop(0)
        return real(layout, lease)

    monkeypatch.setattr("pollen.agent.leases.replay_restores", failing)
    async with running(device, clock):
        session = await queen.next_session()
        lease = await open_lease(queen, session)
        cancel = CellTeardownRequest(
            cell_id=queen.cell_id,
            lease_id=lease.lease_id,
            urgency=Urgency.IMMEDIATE,
            cause=ReleaseCause.CANCELLED,
            reason="A test cancels it.",
        )
        await session.send(cancel, sender=queen.warden_id)
        done = await session.expect(LeaseReleased)  # Only the third attempt gets as far as this.
        assert done.payload.is_restored and done.payload.cause is ReleaseCause.CANCELLED
    assert "pollen.release_failed" in caplog.text and "error=OSError" in caplog.text
    assert "pollen.release_bug" in caplog.text and "error=ValueError" in caplog.text
    assert "secret" not in caplog.text and "shadow" not in caplog.text
