"""Test background processes through a real packet: start, query, stop, and the backstop.

A background process is a program a lease starts and leaves running (gateway-contract section 9.3):
no timeout, no output stream, its output going to a log file when one is named. The Pollen Packet
(the thin gateway program on the device) records the process in the lease table before it answers
a start, so a crash right after cannot leak it, stops only processes this lease started, and kills
every one of them when the lease is released. These tests start real programs and check the
answers, the log, the record, the refusal to touch a process the lease did not start, and the
cleanup.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.process end to end through a
    running packet.

Key invariants:
    - A test that starts a process also stops it, or relies on the release the fixture performs
      when the packet stops.
    - The pid a test signals is always one the packet reported.

See Also:
    - docs/waggle/gateway-contract.md section 9.3.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest
from device_kit import Live, Terms, live_lease, make_device
from queen_double import QueenDouble
from scenarios import eventually, python_argv, teardown

from pollen.errors import StateError
from pollen.lease import DeviceLease, LeaseTable, ProcessKind, RestoreKind
from waggle.clock import Clock
from waggle.ids import LeaseId
from waggle.messages.cell import LeaseReleased
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel
from waggle.messages.session import ProcessAction, SessionProcess, SessionProcessState

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="process groups")
SLEEPER = python_argv("import time; time.sleep(60)")
EOL = os.linesep.encode()  # How print() ends a line on this host.
# A process that always exists and that no lease started: init on POSIX, the System process on
# Windows (which has no pid 1).
SYSTEM_PID = 4 if sys.platform == "win32" else 1


def _request(live: Live, action: ProcessAction, **fields: object) -> SessionProcess:
    """Build a session.process request for the fixture's lease."""
    values: dict[str, object] = {
        "lease_id": live.lease.lease_id,
        "action": action,
        "argv": (),
        "cwd": None,
        "env": (),
        "log_path": None,
        "pid": None,
        "task_id": None,
        "worker_id": None,
    }
    values.update(fields)
    return SessionProcess(**values)


async def _ask(live: Live, message: SessionProcess) -> SessionProcessState:
    """Send a request as the Warden and return the state the packet answered with."""
    sent = await live.session.send(message, sender=live.queen.warden_id)
    answer = await live.session.expect(
        SessionProcessState, where=lambda e: e.correlation_id == sent.id
    )
    return answer.payload


async def _refused(live: Live, message: SessionProcess) -> str:
    """Send a request as the Warden and return the code of the error that refused it."""
    sent = await live.session.send(message, sender=live.queen.warden_id)
    answer = await live.session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
    return answer.payload.code


async def _start(
    live: Live, argv: tuple[str, ...] = SLEEPER, **fields: object
) -> SessionProcessState:
    """Start a background process and return the state the packet reported."""
    return await _ask(live, _request(live, ProcessAction.START, argv=argv, **fields))


async def test_a_started_process_is_recorded_running_and_reported(live: Live) -> None:
    state = await _start(live)
    assert (state.action, state.is_running, state.argv) == (ProcessAction.START, True, SLEEPER)
    held = LeaseTable.load(live.device.layout.lease_table).get(live.lease.lease_id)
    assert held is not None
    assert [(p.pid, p.kind) for p in held.processes] == [(state.pid, ProcessKind.BACKGROUND)]
    assert psutil.pid_exists(state.pid)


async def test_a_process_can_be_queried_and_then_stopped(live: Live) -> None:
    state = await _start(live)
    queried = await _ask(live, _request(live, ProcessAction.QUERY, pid=state.pid))
    assert queried.is_running
    stopped = await _ask(live, _request(live, ProcessAction.STOP, pid=state.pid))
    assert (stopped.is_running, stopped.was_running) == (False, True)
    await eventually(lambda: _gone(state.pid))
    again = await _ask(live, _request(live, ProcessAction.STOP, pid=state.pid))
    assert again.was_running is False


def _is_zombie(pid: int) -> bool:
    """Say whether ``pid`` is only a zombie (dead, awaiting its parent's wait)."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


async def test_output_goes_to_the_log_path_and_nowhere_else(live: Live) -> None:
    code = "print('to the log', flush=True)"
    state = await _start(live, python_argv(code), log_path="out.log")
    assert state.log_path is not None
    log = Path(state.log_path)
    assert log.parent == Path(live.lease.scratch_root)
    await eventually(lambda: log.exists() and b"to the log" in log.read_bytes())


async def test_a_process_a_frozen_packet_starts_gets_the_devices_loader_path_not_the_unpacked_one(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What a single-file build's bootloader does to its own environment, and keeps a copy of.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unpacked/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")
    code = "import os; print(os.environ.get('LD_LIBRARY_PATH'), flush=True)"

    state = await _start(live, python_argv(code), log_path="loader.log")

    assert state.log_path is not None
    log = Path(state.log_path)
    await eventually(lambda: log.exists() and log.read_bytes() == b"/opt/lib" + EOL)


async def test_a_pid_the_lease_did_not_start_is_not_found(live: Live) -> None:
    for action in (ProcessAction.QUERY, ProcessAction.STOP):
        refused = await _refused(live, _request(live, action, pid=SYSTEM_PID))
        assert refused == "pollen.not_found"
    assert psutil.pid_exists(SYSTEM_PID)


async def test_a_program_that_cannot_start_is_a_start_failure(live: Live) -> None:
    refused = await _refused(live, _request(live, ProcessAction.START, argv=("/no/such/program",)))
    assert refused == "pollen.start_failed"


@posix_only
async def test_releasing_the_lease_kills_its_background_processes(live: Live) -> None:
    state = await _start(live)
    released = (await teardown(live.queen, live.session, live.lease.lease_id)).payload
    assert isinstance(released, LeaseReleased)
    assert released.killed_processes == 1
    await eventually(lambda: not psutil.pid_exists(state.pid) or _is_zombie(state.pid))


async def test_a_process_that_exits_at_once_is_reported_not_running(live: Live) -> None:
    state = await _start(live, python_argv("pass"))
    await eventually(lambda: _gone(state.pid))
    queried = await _ask(live, _request(live, ProcessAction.QUERY, pid=state.pid))
    assert queried.is_running is False


def _gone(pid: int) -> bool:
    """Say whether ``pid`` no longer runs (absent, or a zombie)."""
    return not psutil.pid_exists(pid) or _is_zombie(pid)


def _alive_with(marker: str) -> bool:
    """Say whether any live process on the host has ``marker`` in its command line."""
    for process in psutil.process_iter(["cmdline", "status"]):
        if process.info["status"] == psutil.STATUS_ZOMBIE:
            continue
        if marker in " ".join(process.info["cmdline"] or ()):
            return True
    return False


async def test_a_start_in_a_directory_outside_scratch_needs_the_outside_grant(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(access=AccessLevel.SCRATCH))
    async with live_lease(queen, device, clock, access=AccessLevel.SCRATCH) as live:
        request = _request(live, ProcessAction.START, argv=SLEEPER, cwd=str(tmp_path))
        assert await _refused(live, request) == "pollen.capability_denied"
        assert LeaseTable.load(device.layout.lease_table).process_count() == 0


async def test_a_log_outside_scratch_is_recorded_first_and_removed_on_release(
    live: Live, tmp_path: Path
) -> None:
    log = tmp_path / "outside.log"
    state = await _start(live, python_argv("print('to the log', flush=True)"), log_path=str(log))
    await eventually(lambda: log.exists() and b"to the log" in log.read_bytes())
    held = LeaseTable.load(live.device.layout.lease_table).get(live.lease.lease_id)
    assert held is not None
    assert [(Path(r.path), r.kind) for r in held.restores] == [
        (log.resolve(), RestoreKind.CREATED_FILE)
    ]
    assert state.log_path == str(log.resolve())
    released = (await teardown(live.queen, live.session, live.lease.lease_id)).payload
    assert isinstance(released, LeaseReleased) and released.is_restored
    assert not log.exists()


async def test_a_log_that_cannot_be_opened_is_a_start_failure(live: Live) -> None:
    (Path(live.lease.scratch_root) / "logs").mkdir()  # A directory cannot be appended to.
    request = _request(live, ProcessAction.START, argv=SLEEPER, log_path="logs")
    assert await _refused(live, request) == "pollen.start_failed"


async def test_a_lease_tracks_only_so_many_processes(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pollen.executors.process.MAX_TRACKED_PROCESSES", 1)
    await _start(live)
    request = _request(live, ProcessAction.START, argv=SLEEPER)
    assert await _refused(live, request) == "pollen.start_failed"


async def test_a_process_the_lease_cannot_record_is_killed_and_not_left_running(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = LeaseTable.modify
    failures = [StateError("The table cannot be written.")]

    async def failing_once(
        table: LeaseTable, lease_id: LeaseId, change: Callable[[DeviceLease], DeviceLease]
    ) -> DeviceLease:
        if failures:
            raise failures.pop()
        return await real(table, lease_id, change)

    monkeypatch.setattr(LeaseTable, "modify", failing_once)
    marker = f"unrecorded_{os.urandom(4).hex()}"
    request = _request(
        live, ProcessAction.START, argv=python_argv(f"import time; time.sleep(60)  # {marker}")
    )
    assert await _refused(live, request) == "pollen.start_failed"
    await eventually(lambda: not _alive_with(marker))
