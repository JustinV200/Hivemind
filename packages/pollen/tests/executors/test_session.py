"""Test the exec semantics a Warden relies on, through a real packet running real programs.

A Warden (the per-Cell supervisor) drives a Real Cell (an existing device the Hive borrows)
through its Pollen Packet exactly as it would a local terminal, so the packet carries out the
semantics of the ``CellSession`` every Cell offers (gateway-contract section 11): argument lists
only, the output of both streams in order with a final chunk each, exactly one ``session.exit`` per
exec with the right outcome (exited, signalled, timed out, killed), a program that cannot start
exiting 127 with a line on stderr, every program in a process group of its own that dies as a
whole, an environment overlay for that program only, and stdin fed in order with signals that
cannot queue behind bytes. Each test runs a real program (this interpreter, which every host has)
and reads what the scripted Queen receives.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.session and
    pollen.executors.run end to end through a running packet.

Key invariants:
    - A test waits for the frame that ends the exec; it never sleeps to "let the program finish".
    - Anything a test starts it also ends: the lease fixture's packet is stopped on teardown,
      which kills whatever is left.

See Also:
    - docs/waggle/gateway-contract.md section 11 for the semantics under test.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psutil
import pytest
from device_kit import Live, Terms, live_lease, make_device
from disk import resolved
from frame_overrides import Overrides
from queen_double import QueenDouble
from scenarios import ExecOptions, eventually, exec_message, python_argv, run_exec

from pollen.executors.run import ExecRun
from pollen.lease import LeaseTable, ProcessKind
from waggle.clock import Clock
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel
from waggle.messages.session import (
    ProcessSignal,
    SessionClose,
    SessionExit,
    SessionOpen,
    SessionOutcome,
    SessionOutput,
    SessionRequestKind,
    SessionStdin,
    SessionStream,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="process groups and signals")


async def test_opening_a_session_completes(live: Live) -> None:
    request = await live.session.send(
        SessionOpen(lease_id=live.lease.lease_id, reason="A test opens the session."),
        sender=live.queen.warden_id,
    )
    ended = await live.session.expect(SessionExit, where=lambda e: e.correlation_id == request.id)
    assert ended.payload.request_kind is SessionRequestKind.OPEN
    assert ended.payload.outcome is SessionOutcome.COMPLETED


async def test_both_streams_arrive_in_order_and_each_ends_with_a_final_chunk(live: Live) -> None:
    code = "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.stdout.flush()"
    result = await run_exec(live.queen, live.session, live.lease.lease_id, python_argv(code))
    assert (result.stdout, result.stderr) == (b"out", b"err")
    assert (result.exit.outcome, result.exit.exit_code) == (SessionOutcome.EXITED, 0)
    for stream in (SessionStream.STDOUT, SessionStream.STDERR):
        chunks = [o for o in result.outputs if o.stream is stream]
        assert chunks[0].offset == 0
        assert chunks[-1].final and not any(c.final for c in chunks[:-1])
    frames = [e.payload for e in live.session.frames]
    assert frames.index(result.exit) > max(frames.index(o) for o in result.outputs)


async def test_a_nonzero_exit_code_is_reported(live: Live) -> None:
    result = await run_exec(
        live.queen, live.session, live.lease.lease_id, python_argv("raise SystemExit(7)")
    )
    assert (result.exit.outcome, result.exit.exit_code) == (SessionOutcome.EXITED, 7)


async def test_a_program_that_cannot_start_exits_127_with_a_line_on_stderr(live: Live) -> None:
    result = await run_exec(
        live.queen, live.session, live.lease.lease_id, ["/definitely/not/a/program"]
    )
    assert (result.exit.outcome, result.exit.exit_code) == (SessionOutcome.EXITED, 127)
    assert result.stderr.endswith(b"\n") and result.stderr.count(b"\n") == 1


@posix_only
async def test_a_program_ended_by_a_foreign_signal_is_signalled(live: Live) -> None:
    code = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    result = await run_exec(live.queen, live.session, live.lease.lease_id, python_argv(code))
    assert (result.exit.outcome, result.exit.signal_name) == (SessionOutcome.SIGNALED, "SIGKILL")


async def test_a_program_past_its_timeout_is_timed_out(live: Live) -> None:
    result = await run_exec(
        live.queen,
        live.session,
        live.lease.lease_id,
        python_argv("import time; time.sleep(60)"),
        ExecOptions(timeout_s=0.3),
    )
    assert result.exit.outcome is SessionOutcome.TIMED_OUT


async def test_output_past_the_cap_kills_the_program(live: Live) -> None:
    code = "import sys\nwhile True: sys.stdout.write('x' * 4096); sys.stdout.flush()"
    result = await run_exec(
        live.queen,
        live.session,
        live.lease.lease_id,
        python_argv(code),
        ExecOptions(max_output_bytes=10_000),
    )
    assert result.exit.outcome is SessionOutcome.KILLED
    assert "cap" in result.exit.reason
    assert len(result.stdout) <= 10_000


@posix_only
async def test_the_children_of_a_killed_program_die_with_its_group(live: Live) -> None:
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)"
    )
    request = await live.session.send(
        exec_message(live.lease.lease_id, python_argv(code), ExecOptions(timeout_s=0.8)),
        sender=live.queen.warden_id,
    )
    first = await live.session.expect(SessionOutput, where=lambda e: e.correlation_id == request.id)
    child = int(first.payload.chunk.split()[0])
    ended = await live.session.expect(SessionExit, where=lambda e: e.correlation_id == request.id)
    assert ended.payload.outcome is SessionOutcome.TIMED_OUT
    await eventually(lambda: not psutil.pid_exists(child) or _is_zombie(child))


def _is_zombie(pid: int) -> bool:
    """Say whether ``pid`` is only a zombie (dead, awaiting its parent's wait)."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


async def test_the_environment_overlay_applies_to_that_program_only(live: Live) -> None:
    code = "import os; print(os.environ.get('POLLEN_TEST_VALUE'))"
    with_env = await run_exec(
        live.queen,
        live.session,
        live.lease.lease_id,
        python_argv(code),
        ExecOptions(env=[("POLLEN_TEST_VALUE", "overlay")]),
    )
    without = await run_exec(live.queen, live.session, live.lease.lease_id, python_argv(code))
    assert (with_env.stdout.strip(), without.stdout.strip()) == (b"overlay", b"None")


async def test_the_default_directory_is_the_leases_scratch(live: Live) -> None:
    result = await run_exec(
        live.queen,
        live.session,
        live.lease.lease_id,
        python_argv("import os; print(os.getcwd())"),
    )
    assert resolved(Path(result.stdout.decode().strip())) == Path(live.lease.scratch_root)


async def test_the_pid_is_recorded_before_the_first_output_frame(live: Live) -> None:
    code = "import time; print('up', flush=True); time.sleep(60)"
    request = await live.session.send(
        exec_message(live.lease.lease_id, python_argv(code), ExecOptions(timeout_s=5.0)),
        sender=live.queen.warden_id,
    )
    await live.session.expect(SessionOutput, where=lambda e: e.correlation_id == request.id)
    table = LeaseTable.load(live.device.layout.lease_table)
    held = table.get(live.lease.lease_id)
    assert held is not None
    assert [p.kind for p in held.processes] == [ProcessKind.EXEC]
    assert psutil.pid_exists(held.processes[0].pid)


async def test_stdin_is_fed_in_order_and_closed_by_the_final_chunk(live: Live) -> None:
    code = "import sys; sys.stdout.write(sys.stdin.read().upper())"
    request = await live.session.send(
        exec_message(live.lease.lease_id, python_argv(code), ExecOptions(has_stdin=True)),
        sender=live.queen.warden_id,
    )
    for offset, (chunk, final) in enumerate([(b"ab", False), (b"cd", False), (b"", True)]):
        stdin = SessionStdin(
            lease_id=live.lease.lease_id,
            exec_id=request.id,
            chunk=chunk,
            offset=offset * 2,
            final=final,
            signal=None,
        )
        await live.session.send(
            stdin, sender=live.queen.warden_id, overrides=Overrides(correlation_id=request.id)
        )
    ended = await live.session.expect(SessionExit, where=lambda e: e.correlation_id == request.id)
    assert ended.payload.outcome is SessionOutcome.EXITED
    out = [e.payload for e in live.session.frames if isinstance(e.payload, SessionOutput)]
    assert b"".join(o.chunk for o in out if o.stream is SessionStream.STDOUT) == b"ABCD"


@posix_only
@pytest.mark.parametrize("which", list(ProcessSignal))
async def test_a_stdin_signal_ends_the_program_as_killed(live: Live, which: ProcessSignal) -> None:
    code = "import time; print('up', flush=True); time.sleep(60)"
    request = await live.session.send(
        exec_message(
            live.lease.lease_id, python_argv(code), ExecOptions(has_stdin=True, timeout_s=30)
        ),
        sender=live.queen.warden_id,
    )
    await live.session.expect(SessionOutput, where=lambda e: e.correlation_id == request.id)
    signal_message = SessionStdin(
        lease_id=live.lease.lease_id, exec_id=request.id, chunk=b"", offset=0, signal=which
    )
    await live.session.send(
        signal_message, sender=live.queen.warden_id, overrides=Overrides(correlation_id=request.id)
    )
    ended = await live.session.expect(SessionExit, where=lambda e: e.correlation_id == request.id)
    assert ended.payload.outcome is SessionOutcome.KILLED


async def test_several_execs_run_at_once_in_one_session(live: Live) -> None:
    slow = exec_message(
        live.lease.lease_id, python_argv("import time; time.sleep(0.4); print('a')")
    )
    fast = exec_message(live.lease.lease_id, python_argv("print('b')"))
    first = await live.session.send(slow, sender=live.queen.warden_id)
    second = await live.session.send(fast, sender=live.queen.warden_id)
    done_fast = await live.session.expect(
        SessionExit, where=lambda e: e.correlation_id == second.id
    )
    done_slow = await live.session.expect(SessionExit, where=lambda e: e.correlation_id == first.id)
    frames = live.session.frames
    assert frames.index(done_fast.envelope) < frames.index(done_slow.envelope)


async def test_an_exec_on_an_unknown_lease_is_refused(live: Live) -> None:
    other = live.lease.lease_id[:-1] + ("A" if live.lease.lease_id[-1] != "A" else "B")
    sent = await live.session.send(
        exec_message(other, python_argv("print(1)")), sender=live.queen.warden_id
    )
    refused = await live.session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
    assert refused.payload.code == "hive.session.lease_not_open"
    assert refused.payload.failed_kind == "session.exec"


@posix_only
async def test_the_packets_own_environment_is_the_programs_base(live: Live) -> None:
    result = await run_exec(
        live.queen,
        live.session,
        live.lease.lease_id,
        python_argv("import os; print(os.environ.get('PATH') is not None)"),
    )
    assert result.stdout.strip() == str(os.environ.get("PATH") is not None).encode()


async def test_opening_a_session_on_a_lease_that_is_not_open_is_refused(live: Live) -> None:
    other = live.lease.lease_id[:-1] + ("A" if live.lease.lease_id[-1] != "A" else "B")
    sent = await live.session.send(
        SessionOpen(lease_id=other, reason="A test opens it."), sender=live.queen.warden_id
    )
    refused = await live.session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
    assert refused.payload.code == "hive.session.lease_not_open"
    assert refused.payload.failed_kind == "session.open"


def _is_closed(live: Live) -> bool:
    """Say whether the lease table on disk holds the lease with its session closed."""
    held = LeaseTable.load(live.device.layout.lease_table).get(live.lease.lease_id)
    return held is not None and held.is_session_closed


async def test_a_closed_session_is_opened_again_by_a_new_open(live: Live) -> None:
    close = SessionClose(lease_id=live.lease.lease_id, reason="A test closes the session.")
    await live.session.send(close, sender=live.queen.warden_id)
    await eventually(lambda: _is_closed(live))
    reopen = SessionOpen(lease_id=live.lease.lease_id, reason="A test opens it again.")
    sent = await live.session.send(reopen, sender=live.queen.warden_id)
    ended = await live.session.expect(SessionExit, where=lambda e: e.correlation_id == sent.id)
    assert ended.payload.outcome is SessionOutcome.COMPLETED and not _is_closed(live)
    result = await run_exec(
        live.queen, live.session, live.lease.lease_id, python_argv("print('back')")
    )
    assert result.stdout.strip() == b"back"


async def test_a_bug_in_the_packet_ends_the_exec_as_killed_and_logs_only_its_type(
    live: Live, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(_run: ExecRun) -> None:
        raise RuntimeError("secret detail about /etc/shadow")

    monkeypatch.setattr(ExecRun, "execute", broken)
    caplog.set_level(logging.ERROR, logger="pollen.executors.session")
    result = await run_exec(
        live.queen, live.session, live.lease.lease_id, python_argv("print('never runs')")
    )
    assert (result.exit.outcome, result.exit.reason) == (
        SessionOutcome.KILLED,
        "The packet failed while running it.",
    )
    assert "pollen.exec_failed error=RuntimeError" in caplog.text
    assert "shadow" not in caplog.text and "secret" not in caplog.text


async def test_a_working_directory_outside_scratch_needs_the_outside_grant(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(access=AccessLevel.SCRATCH))
    async with live_lease(queen, device, clock, access=AccessLevel.SCRATCH) as scoped:
        options = ExecOptions(cwd=str(tmp_path))
        message = exec_message(scoped.lease.lease_id, python_argv("print(1)"), options)
        sent = await scoped.session.send(message, sender=queen.warden_id)
        refused = await scoped.session.expect(
            ErrorMessage, where=lambda e: e.correlation_id == sent.id
        )
        assert refused.payload.code == "pollen.capability_denied"


async def test_a_full_access_lease_may_run_in_a_directory_outside_scratch(
    live: Live, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = await run_exec(
        live.queen,
        live.session,
        live.lease.lease_id,
        python_argv("import os; print(os.getcwd())"),
        ExecOptions(cwd=str(elsewhere)),
    )
    assert resolved(Path(result.stdout.decode().strip())) == resolved(elsewhere)
