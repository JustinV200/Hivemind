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

import sys
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell.local.process import EXIT_COMMAND_NOT_STARTED, ProcessContext, run_child_process
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.session import ExecSpec, ExitStatus, OutputChunk, OutputStream
from waggle.clock import SystemClock

# A tiny quota, deliberately smaller than the payload _write_bytes_script below writes: with a
# real ScratchQuota this would trip the watchdog; the point of test_no_quota_* is that it never
# does when ctx.quota is None.
_TINY_QUOTA_BYTES = 8


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
