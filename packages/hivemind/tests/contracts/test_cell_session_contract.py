"""Contract suite for CellSession: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.cell.session.CellSession contract and runs against every implementation registered
    in `_HARNESSES` below: `hivemind.cell.fake.FakeSession` (phase 3 step 3.10),
    `hivemind.cell.local.LocalProcessSession`, the Hive Stand's own session (phase 3 step 3.11),
    and `hivemind.cell.in_cell.InCellSession`, for code already running inside its own Virtual
    Cell (roadmap step 5.5). A new CellSession implementation adds a `SessionHarness` here and
    must pass this suite before it is used anywhere else (codingrules 14.3).

    Every case name a harness supports maps to a fixed, kind-independent outcome (`_LOCAL_SCRIPTS`
    keys below): "echo" writes `_STDOUT_TEXT` to stdout and exits 0, "both" writes both
    `_STDOUT_TEXT` and `_STDERR_TEXT` and exits 0, "nonzero" exits `_NONZERO_EXIT`, "sleep" never
    returns before its timeout. A harness decides *how* its session produces that outcome (a
    scripted responder for the fake, a real `sys.executable -c ...` child for the Hive Stand); the
    test bodies only ever assert the outcome.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.session for the CellSession protocol under test.
    - hivemind.cell.fake for FakeSession, the first implementation registered here.
    - hivemind.cell.local for LocalProcessSession, the Hive Stand's own implementation.
    - hivemind.cell.in_cell for InCellSession, the in-Cell implementation (roadmap step 5.5).
    - packages/hivemind/tests/contracts/test_pheromone_trail_contract.py for the pattern this
      suite's harness-per-implementation shape mirrors.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Protocol

import pytest
from builders.cells import make_hive_stand_releaser, make_real_cell_lease

from hivemind.cell import RealCellLease
from hivemind.cell.errors import (
    BackgroundStartError,
    CommandTimeoutError,
    PathNotAllowedError,
    SessionClosedError,
)
from hivemind.cell.fake import FakeSession, FakeStart
from hivemind.cell.in_cell import InCellLeaseReleaser, InCellSession
from hivemind.cell.local.process import EXIT_COMMAND_NOT_STARTED
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cell.session import (
    BackgroundProcess,
    BackgroundSpec,
    CellSession,
    CompletedCommand,
    ExecSpec,
    ExitStatus,
    run,
)
from waggle.clock import FakeClock, SystemClock

_STDOUT_TEXT = b"hello"
_STDERR_TEXT = b"boom"
_NONZERO_EXIT = 7
_SHORT_TIMEOUT_S = 0.5  # The one test that actually waits for a timeout; kept short on purpose.
_LOCAL_QUOTA_BYTES = 64 * 1024 * 1024  # Generous: this suite is about the CellSession contract.
# An executable no host has, for the "missing" case: the local harness really tries to spawn it.
_MISSING_EXECUTABLE = "hivemind-no-such-executable-3f9a2c"


class SessionHarness(Protocol):
    """Build a CellSession of one kind, and the ExecSpec argv for one of `_CASES`."""

    def make_session(self, tmp_path: Path, allowed_paths: tuple[Path, ...] = ()) -> CellSession:
        """Build a fresh session scoped to `tmp_path`, with `allowed_paths` reachable too."""
        ...

    def command_for(self, case: str) -> tuple[str, ...]:
        """Return the ExecSpec argv this implementation answers `case` with."""
        ...

    def widen(self, session: CellSession, path: Path) -> None:
        """Make `path` reachable for an already-built `session`, the way a lease is widened."""
        ...

    def background_for(self, session: CellSession, case: str) -> tuple[str, ...]:
        """Return the BackgroundSpec argv for one of `_BACKGROUND_CASES` (scripting the fake)."""
        ...

    def crash(self, session: CellSession, process: BackgroundProcess) -> None:
        """Make a started process exit on its own, the way a crash would."""
        ...


class _FakeHarness:
    """Builds a FakeSession scripted to answer each case with its fixed, contract-wide outcome."""

    def make_session(self, tmp_path: Path, allowed_paths: tuple[Path, ...] = ()) -> CellSession:
        responder = {
            "echo": CompletedCommand(exit_code=0, stdout=_STDOUT_TEXT, stderr=b"", duration_s=0.0),
            "both": CompletedCommand(
                exit_code=0, stdout=_STDOUT_TEXT, stderr=_STDERR_TEXT, duration_s=0.0
            ),
            "nonzero": CompletedCommand(
                exit_code=_NONZERO_EXIT, stdout=b"", stderr=b"", duration_s=0.0
            ),
            "missing": CompletedCommand(
                exit_code=EXIT_COMMAND_NOT_STARTED,
                stdout=b"",
                stderr=f"{_MISSING_EXECUTABLE}: not found".encode(),
                duration_s=0.0,
            ),
        }
        return FakeSession(
            tmp_path,
            FakeClock(),
            responder=responder,
            allowed_paths=allowed_paths,
            slow_commands=frozenset({"sleep"}),
        )

    def command_for(self, case: str) -> tuple[str, ...]:
        return (case,)

    def widen(self, session: CellSession, path: Path) -> None:
        assert isinstance(session, FakeSession)
        session.allow_path(path)

    def background_for(self, session: CellSession, case: str) -> tuple[str, ...]:
        assert isinstance(session, FakeSession)
        # "logs" writes _BACKGROUND_LOG to its log and keeps running; "missing" cannot start.
        session.script_start("logs", FakeStart(log=_BACKGROUND_LOG))
        session.script_start("missing", FakeStart(fails="No such file or directory"))
        return (case,)

    def crash(self, session: CellSession, process: BackgroundProcess) -> None:
        assert isinstance(session, FakeSession)
        session.exit_process(process)


# One small `python -c` script per case, producing the exact same fixed outcome _FakeHarness's
# responder maps a case to -- so a test body cannot tell which harness ran it. `sys.stdout.buffer`/
# `sys.stderr.buffer` (not print) so no platform's newline translation can perturb the exact bytes
# _STDOUT_TEXT/_STDERR_TEXT compare equal to.
_LOCAL_SCRIPTS = {
    "echo": f"import sys; sys.stdout.buffer.write({_STDOUT_TEXT!r})",
    "both": (
        f"import sys; sys.stdout.buffer.write({_STDOUT_TEXT!r}); "
        f"sys.stderr.buffer.write({_STDERR_TEXT!r})"
    ),
    "nonzero": f"import sys; sys.exit({_NONZERO_EXIT})",
    "sleep": f"import time; time.sleep({_SHORT_TIMEOUT_S * 100})",  # Outlives the short timeout.
}

# Background cases (roadmap step 6.4): "runs" stays up until stopped; "logs" writes
# _BACKGROUND_LOG to stdout, then stays up; "forks" starts a grandchild in the same process group
# and exits, the daemon shape a display server's launcher has; "missing" cannot start at all.
_BACKGROUND_LOG = b"ready 99\n"
_BACKGROUND_SCRIPTS = {
    "runs": "import time; time.sleep(600)",
    "logs": (
        f"import sys, time; sys.stdout.buffer.write({_BACKGROUND_LOG!r}); sys.stdout.flush(); "
        "time.sleep(600)"
    ),
    "forks": (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])"
    ),
}


class _LocalHarness:
    """Builds a LocalProcessSession over a real, unopened RealCellLease and a real subprocess."""

    def __init__(self) -> None:
        self._leases: dict[int, RealCellLease] = {}

    def make_session(self, tmp_path: Path, allowed_paths: tuple[Path, ...] = ()) -> CellSession:
        # A real SystemClock: this harness spawns real child processes, whose exit and timeout
        # timing cannot be driven by a FakeClock. note_started_process/note_touched_path never
        # check lease.state, so the lease need not be open()ed for a session to use it here.
        clock = SystemClock()
        lease = make_real_cell_lease(
            tmp_path,
            clock=clock,
            releaser=make_hive_stand_releaser(clock=clock),
            allowed_paths=allowed_paths,
        )
        session = LocalProcessSession(lease, ScratchQuota(quota_bytes=_LOCAL_QUOTA_BYTES), clock)
        self._leases[id(session)] = lease
        return session

    def widen(self, session: CellSession, path: Path) -> None:
        self._leases[id(session)].note_allowed_path(path)

    def command_for(self, case: str) -> tuple[str, ...]:
        if case == "missing":
            return (_MISSING_EXECUTABLE,)  # Not a script: the point is that nothing can run it.
        return (sys.executable, "-c", _LOCAL_SCRIPTS[case])

    def background_for(self, session: CellSession, case: str) -> tuple[str, ...]:
        return _real_background_argv(case)

    def crash(self, session: CellSession, process: BackgroundProcess) -> None:
        _kill_leader(process)


class _InCellHarness:
    """Builds an InCellSession over a real, unopened RealCellLease and a real subprocess.

    Shares `_LOCAL_SCRIPTS`/`command_for` with `_LocalHarness`: the outcome an InCellSession
    produces for each case is identical to LocalProcessSession's -- both run the exact same
    `python -c` script -- which is the point of one contract suite covering both.
    """

    def __init__(self) -> None:
        self._leases: dict[int, RealCellLease] = {}

    def make_session(self, tmp_path: Path, allowed_paths: tuple[Path, ...] = ()) -> CellSession:
        # A real SystemClock, matching _LocalHarness's own reasoning: real child processes, real
        # exit and timeout timing.
        clock = SystemClock()
        lease = make_real_cell_lease(
            tmp_path,
            clock=clock,
            releaser=InCellLeaseReleaser(clock),
            allowed_paths=allowed_paths,
        )
        session = InCellSession(lease, clock)
        self._leases[id(session)] = lease
        return session

    def widen(self, session: CellSession, path: Path) -> None:
        self._leases[id(session)].note_allowed_path(path)

    def command_for(self, case: str) -> tuple[str, ...]:
        if case == "missing":
            return (_MISSING_EXECUTABLE,)
        return (sys.executable, "-c", _LOCAL_SCRIPTS[case])

    def background_for(self, session: CellSession, case: str) -> tuple[str, ...]:
        return _real_background_argv(case)

    def crash(self, session: CellSession, process: BackgroundProcess) -> None:
        _kill_leader(process)


def _real_background_argv(case: str) -> tuple[str, ...]:
    """The argv a real session starts for a background case."""
    if case == "missing":
        return (_MISSING_EXECUTABLE,)
    return (sys.executable, "-c", _BACKGROUND_SCRIPTS[case])


def _kill_leader(process: BackgroundProcess) -> None:
    """Kill only a background process's leader, the way a crash ends it (not its group)."""
    # SIGKILL on POSIX; os.kill with SIGTERM terminates the process on Windows.
    os.kill(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


_HARNESSES: dict[str, SessionHarness] = {
    "fake": _FakeHarness(),
    "in_cell": _InCellHarness(),
    "local": _LocalHarness(),
}


@pytest.fixture(params=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> SessionHarness:
    """One SessionHarness per registered CellSession implementation."""
    return _HARNESSES[request.param]


async def test_exec_ends_in_exactly_one_exit_status_after_its_output(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)

    events = [event async for event in session.exec(ExecSpec(argv=harness.command_for("both")))]

    assert isinstance(events[-1], ExitStatus)
    assert all(not isinstance(event, ExitStatus) for event in events[:-1])


async def test_run_collects_stdout_and_stderr_on_success(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)

    completed = await run(session, ExecSpec(argv=harness.command_for("both")))

    assert completed.exit_code == 0
    assert completed.stdout == _STDOUT_TEXT
    assert completed.stderr == _STDERR_TEXT


async def test_nonzero_exit_code_surfaces(harness: SessionHarness, tmp_path: Path) -> None:
    session = harness.make_session(tmp_path)

    completed = await run(session, ExecSpec(argv=harness.command_for("nonzero")))

    assert completed.exit_code == _NONZERO_EXIT


async def test_a_command_that_cannot_start_is_a_failed_command_not_an_exception(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)

    completed = await run(session, ExecSpec(argv=harness.command_for("missing")))

    # A shell reports a command it cannot find with exit 127 and a line on stderr; a CellSession
    # does the same, so the bee that proposed it reads an ordinary failure it can correct, instead
    # of an OSError escaping its tool call and crashing the bee (an Alarm, then an escalation).
    assert completed.exit_code == EXIT_COMMAND_NOT_STARTED
    assert completed.stderr


async def test_put_file_then_get_file_round_trips_bytes(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)

    await session.put_file(Path("data.bin"), b"payload")
    result = await session.get_file(Path("data.bin"))

    assert result == b"payload"


async def test_delete_file_removes_a_written_file_and_a_missing_path_raises(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)
    await session.put_file(Path("gone.txt"), b"bye")

    await session.delete_file(Path("gone.txt"))

    # Deleting twice is an error, never a silent no-op, so a rollback that runs twice is
    # visible as a bookkeeping bug rather than hidden.
    with pytest.raises(FileNotFoundError):
        await session.get_file(Path("gone.txt"))
    with pytest.raises(FileNotFoundError):
        await session.delete_file(Path("gone.txt"))


async def test_relative_paths_resolve_under_scratch_dir(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)

    await session.put_file(Path("nested/dir/data.bin"), b"payload")

    assert await session.get_file(session.scratch_dir / "nested" / "dir" / "data.bin") == b"payload"


async def test_path_outside_scratch_raises_unless_allowed(
    harness: SessionHarness, tmp_path: Path
) -> None:
    outside_root = tmp_path.parent / "outside-allowed"
    refusing = harness.make_session(tmp_path)
    allowing = harness.make_session(tmp_path, allowed_paths=(outside_root,))

    with pytest.raises(PathNotAllowedError):
        await refusing.put_file(outside_root / "data.bin", b"payload")
    await allowing.put_file(outside_root / "data.bin", b"payload")  # Does not raise.


async def test_a_path_allowed_after_construction_is_reachable(
    harness: SessionHarness, tmp_path: Path
) -> None:
    """A lease widened after the session exists (keep_root, a declared Leaving) is honoured.

    `hivemind.wardens.spawn` widens the lease per sub-bee once the session already exists, so a
    session must read its allowed paths live, never snapshot them at construction (the merge
    review found `InCellSession` doing exactly that).
    """
    outside_root = tmp_path.parent / "outside-widened"
    session = harness.make_session(tmp_path)
    with pytest.raises(PathNotAllowedError):
        await session.put_file(outside_root / "data.bin", b"payload")

    harness.widen(session, outside_root)
    await session.put_file(outside_root / "data.bin", b"payload")  # Does not raise.


async def test_timeout_raises_command_timeout(harness: SessionHarness, tmp_path: Path) -> None:
    session = harness.make_session(tmp_path)
    spec = ExecSpec(argv=harness.command_for("sleep"), timeout_s=_SHORT_TIMEOUT_S)

    with pytest.raises(CommandTimeoutError):
        await run(session, spec)


async def test_exec_after_close_raises_session_closed(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)
    await session.close()

    with pytest.raises(SessionClosedError):
        await run(session, ExecSpec(argv=harness.command_for("echo")))


async def test_close_is_idempotent(harness: SessionHarness, tmp_path: Path) -> None:
    session = harness.make_session(tmp_path)

    await session.close()
    await session.close()

    assert not session.is_open


# ──────────────────────────────────────────────────────────────────────────────
# Background processes (roadmap step 6.4, ADR-0031)
# ──────────────────────────────────────────────────────────────────────────────

_LOG_WAIT_S = 10.0  # Generous: a real child must start Python and flush one line.
_LOG_POLL_S = 0.05  # How often the log is re-read while waiting for it.


async def _read_log_when_written(session: CellSession, path: Path) -> bytes:
    """Poll `path` through the session until it holds something, bounded by _LOG_WAIT_S."""
    async with asyncio.timeout(_LOG_WAIT_S):
        while True:
            data = await session.get_file(path)
            if data:
                return data
            await asyncio.sleep(_LOG_POLL_S)


async def test_start_returns_a_running_process_and_stop_ends_it(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)

    process = await session.start(BackgroundSpec(argv=harness.background_for(session, "runs")))

    assert process.pid > 0
    assert await session.is_running(process)
    assert await session.stop(process) is True
    assert not await session.is_running(process)
    # Idempotent: stopping twice is a quiet False, never an error.
    assert await session.stop(process) is False
    await session.close()


async def test_a_background_process_writes_its_output_to_its_log_in_scratch(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)
    spec = BackgroundSpec(argv=harness.background_for(session, "logs"), log_path=Path("x/out.log"))

    process = await session.start(spec)

    assert process.log_path == (session.scratch_dir / "x" / "out.log").resolve(strict=False)
    assert await _read_log_when_written(session, Path("x/out.log")) == _BACKGROUND_LOG
    await session.close()


async def test_a_log_path_outside_scratch_is_refused(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)
    spec = BackgroundSpec(
        argv=harness.background_for(session, "runs"), log_path=tmp_path.parent / "elsewhere.log"
    )

    with pytest.raises(PathNotAllowedError):
        await session.start(spec)
    await session.close()


async def test_a_command_that_cannot_start_raises_background_start_error(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)

    # Unlike exec's exit 127, a start is infrastructure asking for something it needs running.
    with pytest.raises(BackgroundStartError):
        await session.start(BackgroundSpec(argv=harness.background_for(session, "missing")))
    await session.close()


async def test_is_running_turns_false_when_the_process_dies_on_its_own(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)
    process = await session.start(BackgroundSpec(argv=harness.background_for(session, "runs")))

    harness.crash(session, process)

    async with asyncio.timeout(_LOG_WAIT_S):
        while True:
            if not await session.is_running(process):
                break
            await asyncio.sleep(_LOG_POLL_S)
    assert await session.stop(process) is False  # It had already exited; still reaped quietly.
    await session.close()


async def test_close_stops_every_background_process(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)
    first = await session.start(BackgroundSpec(argv=harness.background_for(session, "runs")))
    second = await session.start(BackgroundSpec(argv=harness.background_for(session, "runs")))

    await session.close()

    assert not await session.is_running(first)
    assert not await session.is_running(second)


async def test_start_after_close_raises_session_closed(
    harness: SessionHarness, tmp_path: Path
) -> None:
    session = harness.make_session(tmp_path)
    await session.close()

    with pytest.raises(SessionClosedError):
        await session.start(BackgroundSpec(argv=harness.background_for(session, "runs")))
