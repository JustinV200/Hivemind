"""Contract suite for CellSession: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.cell.session.CellSession contract and runs against every implementation registered
    in `_HARNESSES` below: `hivemind.cell.fake.FakeSession` (phase 3 step 3.10) and
    `hivemind.cell.local.LocalProcessSession`, the Hive Stand's own session (phase 3 step 3.11). A
    new CellSession implementation adds a `SessionHarness` here and must pass this suite before it
    is used anywhere else (codingrules 14.3).

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
    - packages/hivemind/tests/contracts/test_pheromone_trail_contract.py for the pattern this
      suite's harness-per-implementation shape mirrors.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

import pytest
from builders.cells import make_real_cell_lease

from hivemind.cell.errors import CommandTimeoutError, PathNotAllowedError, SessionClosedError
from hivemind.cell.fake import FakeSession
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.releaser import HiveStandLeaseReleaser
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cell.session import CellSession, CompletedCommand, ExecSpec, ExitStatus, run
from waggle.clock import FakeClock, SystemClock

_STDOUT_TEXT = b"hello"
_STDERR_TEXT = b"boom"
_NONZERO_EXIT = 7
_SHORT_TIMEOUT_S = 0.5  # The one test that actually waits for a timeout; kept short on purpose.
_LOCAL_QUOTA_BYTES = 64 * 1024 * 1024  # Generous: this suite is about the CellSession contract.


class SessionHarness(Protocol):
    """Build a CellSession of one kind, and the ExecSpec argv for one of `_CASES`."""

    def make_session(self, tmp_path: Path, allowed_paths: tuple[Path, ...] = ()) -> CellSession:
        """Build a fresh session scoped to `tmp_path`, with `allowed_paths` reachable too."""
        ...

    def command_for(self, case: str) -> tuple[str, ...]:
        """Return the ExecSpec argv this implementation answers `case` with."""
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


class _LocalHarness:
    """Builds a LocalProcessSession over a real, unopened RealCellLease and a real subprocess."""

    def make_session(self, tmp_path: Path, allowed_paths: tuple[Path, ...] = ()) -> CellSession:
        # A real SystemClock: this harness spawns real child processes, whose exit and timeout
        # timing cannot be driven by a FakeClock. note_started_process/note_touched_path never
        # check lease.state, so the lease need not be open()ed for a session to use it here.
        clock = SystemClock()
        lease = make_real_cell_lease(
            tmp_path,
            clock=clock,
            releaser=HiveStandLeaseReleaser(clock),
            allowed_paths=allowed_paths,
        )
        return LocalProcessSession(lease, ScratchQuota(quota_bytes=_LOCAL_QUOTA_BYTES), clock)

    def command_for(self, case: str) -> tuple[str, ...]:
        return (sys.executable, "-c", _LOCAL_SCRIPTS[case])


_HARNESSES: dict[str, SessionHarness] = {"fake": _FakeHarness(), "local": _LocalHarness()}


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
