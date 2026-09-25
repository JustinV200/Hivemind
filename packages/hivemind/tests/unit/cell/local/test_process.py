"""Unit tests for hivemind.cell.local.process: the shared child-process machinery (roadmap 5.5).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises `run_child_process`/`ProcessContext`
    directly, ahead of `hivemind.cell.local.session.LocalProcessSession` and `hivemind.cell.
    in_cell.InCellSession` (both covered end to end by
    packages/hivemind/tests/contracts/test_cell_session_contract.py); this module's own focus is
    the seam InCellSession relies on -- `quota=None` disables every quota check.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 5.5 for "reuse the local session's process machinery... rather than
      duplicating it."
    - hivemind.cell.local.process for the module under test.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from hivemind.cell.local.process import EXIT_COMMAND_NOT_STARTED, ProcessContext, run_child_process
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.session import ExecSpec, ExitStatus, OutputChunk, OutputStream
from waggle.clock import SystemClock

# A tiny quota, deliberately smaller than the payload _write_bytes_script below writes: with a
# real ScratchQuota this would trip the watchdog; the point of test_no_quota_* is that it never
# does when ctx.quota is None.
_TINY_QUOTA_BYTES = 8
_LONG_SLEEP_S = 60.0  # Far longer than the test: the child is still running when it is cancelled.
_WAIT_TIMEOUT_S = 5.0  # How long the test waits for a real child to start, and to be gone.


@dataclass(frozen=True, slots=True)
class _Collected:
    """What `_run` gathers from one `run_child_process` call, mirroring `CompletedCommand`."""

    exit_code: int
    stdout: bytes
    stderr: bytes


async def _run(spec: ExecSpec, ctx: ProcessContext) -> _Collected:
    """Drive `run_child_process` to completion and collect its stdout/stderr/exit code."""
    stdout = bytearray()
    stderr = bytearray()
    exit_status: ExitStatus | None = None
    async for event in run_child_process(spec, ctx):
        if isinstance(event, OutputChunk):
            (stdout if event.stream is OutputStream.STDOUT else stderr).extend(event.data)
        else:
            exit_status = event
    assert exit_status is not None
    return _Collected(exit_code=exit_status.code, stdout=bytes(stdout), stderr=bytes(stderr))


def _context(tmp_path: Path, *, quota: ScratchQuota | None, started: list[int]) -> ProcessContext:
    """Build a ProcessContext over `tmp_path`, recording every started pid into `started`."""
    return ProcessContext(
        clock=SystemClock(), scratch_dir=tmp_path, quota=quota, on_started=started.append
    )


async def test_run_child_process_reports_the_started_pid_before_any_output(
    tmp_path: Path,
) -> None:
    started: list[int] = []
    ctx = _context(tmp_path, quota=None, started=started)
    spec = ExecSpec(argv=(sys.executable, "-c", "print('hi')"))

    completed = await _run(spec, ctx)

    assert completed.exit_code == 0
    assert len(started) == 1
    assert started[0] > 0


async def test_no_quota_configured_never_raises_even_over_a_tiny_quota_sized_write(
    tmp_path: Path,
) -> None:
    # A real ScratchQuota this small would trip the watchdog long before this script's stdout
    # (well over _TINY_QUOTA_BYTES) finished writing; ctx.quota=None (InCellSession's own choice,
    # roadmap step 5.5) must never enforce that cap at all.
    started: list[int] = []
    ctx = _context(tmp_path, quota=None, started=started)
    payload = "x" * (_TINY_QUOTA_BYTES * 10)
    spec = ExecSpec(argv=(sys.executable, "-c", f"print({payload!r})"))

    completed = await _run(spec, ctx)

    assert completed.exit_code == 0
    assert payload.encode() in completed.stdout


async def test_a_command_that_cannot_start_yields_a_stderr_chunk_and_exit_127(
    tmp_path: Path,
) -> None:
    started: list[int] = []
    ctx = _context(tmp_path, quota=None, started=started)
    spec = ExecSpec(argv=("hivemind-no-such-executable-9f21ab",))

    completed = await _run(spec, ctx)

    assert completed.exit_code == EXIT_COMMAND_NOT_STARTED
    assert completed.stdout == b""
    assert completed.stderr
    assert not started  # A command that never started never reports a pid.


async def test_events_stream_output_before_the_final_exit_status(tmp_path: Path) -> None:
    started: list[int] = []
    ctx = _context(tmp_path, quota=None, started=started)
    spec = ExecSpec(argv=(sys.executable, "-c", "import sys; sys.stdout.write('hello')"))

    events = [event async for event in run_child_process(spec, ctx)]

    assert isinstance(events[-1], ExitStatus)
    assert any(
        isinstance(event, OutputChunk)
        and event.stream is OutputStream.STDOUT
        and event.data == b"hello"
        for event in events[:-1]
    )


async def test_a_cancelled_run_kills_its_child_before_the_cancel_completes(tmp_path: Path) -> None:
    # Roadmap 10.6c: a bee cancelled mid-command (stopped, respawned, quarantined) must not leave
    # the command running until lease release; the lease is never released in this test.
    started: list[int] = []
    ctx = _context(tmp_path, quota=None, started=started)
    spec = ExecSpec(
        argv=(sys.executable, "-c", f"import time; time.sleep({_LONG_SLEEP_S})"),
        timeout_s=_LONG_SLEEP_S,
    )
    running = asyncio.ensure_future(_run(spec, ctx))
    await _wait_until(lambda: bool(started), _WAIT_TIMEOUT_S)
    assert _is_process_alive(started[0])

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    # Killed by the cancel itself; the poll only waits for the event loop to reap the dead child.
    await _wait_until(lambda: not _is_process_alive(started[0]), _WAIT_TIMEOUT_S)


async def _wait_until(predicate: Callable[[], bool], timeout_s: float) -> None:
    """Poll `predicate` on real time: a real child's start and exit cannot run on a FakeClock."""
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout_s}s")
        await asyncio.sleep(0.01)


def _is_process_alive(pid: int) -> bool:
    """Return whether `pid` is still a live process, POSIX and Windows shims side by side."""
    if sys.platform == "win32":
        # SAFETY: an argument list, never shell=True; tasklist is a read-only query.
        result = subprocess.run(  # noqa: S603 -- fixed argv, a well-known system command.
            ["tasklist", "/FI", f"PID eq {pid}"],  # noqa: S607 -- well-known system command.
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    else:
        # An explicit branch, so mypy checks each platform's half on its own platform only.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
