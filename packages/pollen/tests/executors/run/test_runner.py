"""Test one exec from spawn to exit: its streams, its record in the lease, and how it is stopped.

``session.exec`` starts a program as the leader of a process group of its own, records the group in
the lease table before the first output frame, streams its output, and ends with exactly one
durable ``session.exit`` (gateway-contract section 11). A program that cannot start is not an
error reply: it exits 127 after one stderr line, as a shell would report it. A release can arrive
before the program has even started, a signal can arrive on stdin, and cancelling the run (the
packet is stopping) must leave no process behind. These tests run real programs through the real
shim and a real lease table, with a reporter that records what the Queen would have been sent.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.run.runner.

Key invariants:
    - Whatever a test does, the groups it started are killed when it ends.
    - A run is always finished with a bound, so a hung program fails the test, not the suite.

See Also:
    - docs/waggle/gateway-contract.md section 11.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import psutil
import pytest
from executor_kit import Kit, Limits, make_kit, make_plan, open_lease_for
from fast_clock import FastClock
from scenarios import eventually, python_argv

from pollen.executors.run.runner import EXIT_COMMAND_NOT_STARTED, ExecRun
from pollen.lease import DeviceLease, ProcessKind, ProcessRecord
from pollen.platform.base import ProcessRef
from waggle.ids import new_message_id
from waggle.messages.session import (
    ProcessSignal,
    SessionExit,
    SessionOutcome,
    SessionOutput,
    SessionRequestKind,
    SessionStdin,
    SessionStream,
)

CLOCK = FastClock()
LIMIT_S = 20.0  # A generous bound on a real program, so a hang fails instead of stalling the suite.
UP_THEN_SLEEP = "print('up', flush=True); import time; time.sleep(60)"
EOL = os.linesep.encode()  # How print() ends a line on this host; the packet streams it unchanged.


class Rig:
    """Builds runs over a real lease, and kills whatever a test leaves running."""

    def __init__(self, kit: Kit, lease: DeviceLease) -> None:
        """Bind the rig to a kit and the lease its programs run in."""
        self.kit = kit
        self.lease = lease
        self._tasks: list[asyncio.Task[None]] = []

    def run(self, code: str, limits: Limits | None = None) -> ExecRun:
        """Plan an exec of the Python ``code`` under ``limits``."""
        return self.run_argv(python_argv(code), limits)

    def run_argv(self, argv: tuple[str, ...], limits: Limits | None = None) -> ExecRun:
        """Plan an exec of ``argv`` under ``limits``."""
        return ExecRun(make_plan(self.kit, CLOCK, self.lease, argv, limits), self.kit.deps)

    def start(self, run: ExecRun) -> asyncio.Task[None]:
        """Run ``run`` as a task, the way the session executor does."""
        task = asyncio.create_task(run.execute())
        self._tasks.append(task)
        return task

    async def finish(self, run: ExecRun) -> None:
        """Run ``run`` to its end, within the bound."""
        await asyncio.wait_for(self.start(run), timeout=LIMIT_S)

    def recorded(self) -> tuple[ProcessRecord, ...]:
        """The processes the lease table holds for the lease, as written to disk."""
        held = self.kit.table.get(self.lease.lease_id)
        assert held is not None
        return held.processes

    def only_exit(self) -> SessionExit:
        """The one ``session.exit`` the run sent."""
        exits = self.kit.reporter.of(SessionExit)
        assert len(exits) == 1
        return exits[0]

    def text(self, stream: SessionStream) -> bytes:
        """The bytes sent on ``stream``, joined."""
        chunks = self.kit.reporter.of(SessionOutput)
        return b"".join(c.chunk for c in chunks if c.stream is stream)

    async def close(self) -> None:
        """Kill the groups the table still holds, then wait for every run to end."""
        for lease in self.kit.table.all():
            for record in lease.processes:
                ref = ProcessRef(record.pid, record.created)
                self.kit.deps.shim.signal_group(ref, ProcessSignal.KILL)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    """A kit with an open lease, and a rig that cleans up after the test."""
    kit = make_kit(tmp_path, CLOCK)
    holder = Rig(kit, await open_lease_for(kit, CLOCK))
    yield holder
    await holder.close()


def _gone(pid: int) -> bool:
    """Say whether ``pid`` no longer runs (absent, or a zombie awaiting its parent)."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _stdin(rig: Rig, chunk: bytes, offset: int, *, final: bool = False) -> SessionStdin:
    """Build a stdin event for the rig's lease."""
    return SessionStdin(
        lease_id=rig.lease.lease_id,
        exec_id=new_message_id(CLOCK),
        chunk=chunk,
        offset=offset,
        final=final,
        signal=None,
    )


async def test_a_program_streams_its_output_and_ends_with_one_durable_exit(rig: Rig) -> None:
    run = rig.run("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)")
    await rig.finish(run)
    streams = (rig.text(SessionStream.STDOUT), rig.text(SessionStream.STDERR))
    assert streams == (b"out" + EOL, b"err" + EOL)
    ended = rig.only_exit()
    assert (ended.outcome, ended.exit_code, ended.request_kind) == (
        SessionOutcome.EXITED,
        3,
        SessionRequestKind.EXEC,
    )
    last = rig.kit.reporter.sent[-1]  # After every output frame, and queued durably.
    assert last.payload is ended and last.is_durable
    assert last.correlation_id == ended.request_id and last.to == rig.kit.state.warden_id
    assert run.finished.is_set() and rig.recorded() == ()  # The whole group is gone: forgotten.


SHOW_LOADER = "import os; print(os.environ.get('LD_LIBRARY_PATH'))"
REQUESTED = {"LD_LIBRARY_PATH": "/requested"}  # An ``env`` entry of a ``session.exec`` request.


async def test_a_program_a_frozen_packet_starts_gets_the_devices_loader_path_not_the_unpacked_one(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What a single-file build's bootloader does to its own environment, and keeps a copy of.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unpacked/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")

    await rig.finish(rig.run(SHOW_LOADER))

    assert rig.text(SessionStream.STDOUT) == b"/opt/lib" + EOL


async def test_a_program_a_frozen_packet_starts_has_no_loader_path_when_the_device_had_none(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unpacked/_MEI123")  # Nothing before it: no copy kept.
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    await rig.finish(rig.run(SHOW_LOADER))

    assert rig.text(SessionStream.STDOUT) == b"None" + EOL


async def test_a_single_file_program_a_frozen_packet_starts_runs_as_an_instance_of_its_own(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    code = "import os; print(os.environ.get('PYINSTALLER_RESET_ENVIRONMENT'))"

    await rig.finish(rig.run(code))

    # The packet itself, run by a command in a lease, would otherwise take itself for a worker.
    assert rig.text(SessionStream.STDOUT) == b"1" + EOL


async def test_the_loader_path_a_request_names_still_reaches_its_program(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unpacked/_MEI123")
    plan = replace(make_plan(rig.kit, CLOCK, rig.lease, python_argv(SHOW_LOADER)), env=REQUESTED)

    await rig.finish(ExecRun(plan, rig.kit.deps))

    assert rig.text(SessionStream.STDOUT) == b"/requested" + EOL  # The request's own overlay.


async def test_the_process_is_in_the_lease_table_before_its_first_output(rig: Rig) -> None:
    run = rig.run(UP_THEN_SLEEP)
    task = rig.start(run)
    await rig.kit.reporter.wait_for(SessionOutput)
    assert [r.kind for r in rig.recorded()] == [ProcessKind.EXEC]
    await run.stop("The test is over.", graceful=False)
    await asyncio.wait_for(task, timeout=LIMIT_S)


@pytest.mark.parametrize("argv", [("/no/such/program",), ("bad\0argument",)])
async def test_a_program_that_cannot_start_exits_127_after_one_stderr_line(
    rig: Rig, argv: tuple[str, ...]
) -> None:
    await rig.finish(rig.run_argv(argv))
    ended = rig.only_exit()
    assert (ended.outcome, ended.exit_code) == (SessionOutcome.EXITED, EXIT_COMMAND_NOT_STARTED)
    assert ended.reason == "The program could not start."
    assert rig.text(SessionStream.STDERR).startswith(argv[0].encode())
    assert rig.text(SessionStream.STDOUT) == b"" and rig.recorded() == ()


async def test_a_release_that_overtakes_the_start_ends_the_run_as_killed(rig: Rig) -> None:
    run = rig.run("import time; time.sleep(60)")
    task = rig.start(run)
    was_running = await run.stop("The lease was released.", graceful=False)
    await asyncio.wait_for(task, timeout=LIMIT_S)
    assert was_running is False  # Nothing had started when the stop looked.
    ended = rig.only_exit()
    assert (ended.outcome, ended.exit_code, ended.reason) == (
        SessionOutcome.KILLED,
        None,
        "The lease was released.",
    )


async def test_cancelling_a_run_kills_its_program_and_reports_nothing(rig: Rig) -> None:
    run = rig.run(UP_THEN_SLEEP)
    task = rig.start(run)
    await rig.kit.reporter.wait_for(SessionOutput)
    pid = rig.recorded()[0].pid
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await eventually(lambda: _gone(pid))
    assert run.finished.is_set() and rig.kit.reporter.of(SessionExit) == []


async def test_a_program_that_cannot_be_recorded_is_killed_and_does_not_run(rig: Rig) -> None:
    await rig.kit.table.remove(rig.lease.lease_id)  # The lease is gone from the table.
    await rig.finish(rig.run("import time; time.sleep(60)"))
    ended = rig.only_exit()
    assert (ended.outcome, ended.reason) == (
        SessionOutcome.KILLED,
        "The program could not be recorded.",
    )
    assert rig.text(SessionStream.STDOUT) == b""


async def test_stdin_bytes_and_a_close_reach_the_program(rig: Rig) -> None:
    run = rig.run("import sys; sys.stdout.write(sys.stdin.read().upper())", Limits(has_stdin=True))
    task = rig.start(run)
    run.feed(_stdin(rig, b"ab", 0))
    run.feed(_stdin(rig, b"cd", 2))
    run.feed(_stdin(rig, b"", 4, final=True))
    await asyncio.wait_for(task, timeout=LIMIT_S)
    assert rig.text(SessionStream.STDOUT) == b"ABCD" and rig.only_exit().exit_code == 0


async def test_a_signal_on_stdin_ends_the_program_and_is_the_reason(rig: Rig) -> None:
    run = rig.run(UP_THEN_SLEEP, Limits(has_stdin=True))
    task = rig.start(run)
    await rig.kit.reporter.wait_for(SessionOutput)
    run.feed(
        SessionStdin(
            lease_id=rig.lease.lease_id,
            exec_id=new_message_id(CLOCK),
            chunk=b"",
            offset=0,
            final=False,
            signal=ProcessSignal.TERMINATE,
        )
    )
    await asyncio.wait_for(task, timeout=LIMIT_S)
    ended = rig.only_exit()
    assert (ended.outcome, ended.reason) == (SessionOutcome.KILLED, "A stdin signal ended it.")


async def test_a_run_past_its_timeout_is_timed_out(rig: Rig) -> None:
    await rig.finish(rig.run("import time; time.sleep(60)", Limits(timeout_s=0.3)))
    ended = rig.only_exit()
    assert (ended.outcome, ended.reason) == (
        SessionOutcome.TIMED_OUT,
        "It ran past its 0.3 s timeout.",
    )
    assert rig.recorded() == ()


async def test_output_past_the_cap_kills_the_group_and_is_reported(rig: Rig) -> None:
    code = "import sys, time; sys.stdout.write('x' * 100000); sys.stdout.flush(); time.sleep(60)"
    await rig.finish(rig.run(code, Limits(max_output_bytes=1000)))
    ended = rig.only_exit()
    assert (ended.outcome, ended.reason) == (
        SessionOutcome.KILLED,
        "Output passed its cap; the group was killed.",
    )
    assert rig.text(SessionStream.STDOUT) == b"x" * 1000  # Cut exactly at the cap.


@pytest.mark.xfail(
    sys.platform == "win32",
    reason="Windows follows an exec's tree through its leader (the shim has no Job Object yet, "
    "gateway-contract section 3), so a child the leader leaves behind is not recorded",
    strict=True,
)
async def test_a_child_that_outlives_the_program_stays_recorded_with_the_lease(rig: Rig) -> None:
    code = (
        "import subprocess, sys; subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    )
    await rig.finish(rig.run(code))
    assert rig.only_exit().outcome is SessionOutcome.EXITED
    # The leader is gone but its group is not: the release must still find and kill the child.
    assert [r.kind for r in rig.recorded()] == [ProcessKind.EXEC]
