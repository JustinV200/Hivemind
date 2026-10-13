"""Test SandboxCell over a FakeSession: refusal of a borrowed Cell, paths, exec and cleanup.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/sandbox_cell.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/sandbox_cell.py for the module under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from builders.cells import make_cell

from hivemind.cell.fake import FakeSession
from hivemind.cell.models import CellKind
from hivemind.cell.session import CompletedCommand, ExecSpec
from hivemind.royal_jelly.quarantine_comb import SandboxCell, SandboxPathError, SandboxRefusedError
from hivemind.royal_jelly.quarantine_comb.sandbox import TIMED_OUT_EXIT_CODE
from waggle.clock import FakeClock

_SCRATCH = Path("/scratch")


def _done(stdout: bytes = b"", stderr: bytes = b"", code: int = 0) -> CompletedCommand:
    """Build a scripted command outcome."""
    return CompletedCommand(exit_code=code, stdout=stdout, stderr=stderr, duration_s=0.5)


def _sandbox(
    seen: list[ExecSpec] | None = None, outcome: CompletedCommand | None = None, **kwargs: object
) -> tuple[SandboxCell, FakeSession]:
    """Build a SandboxCell over a virtual Cell and a FakeSession that records execs into `seen`."""
    clock = FakeClock()

    def respond(spec: ExecSpec) -> CompletedCommand:
        if seen is not None:
            seen.append(spec)
        return outcome or _done()

    session = FakeSession(_SCRATCH, clock, responder=respond)
    cell = make_cell(CellKind.VIRTUAL, clock)
    return SandboxCell(cell, session, clock, **kwargs), session  # type: ignore[arg-type]  # kwargs.


def test_a_borrowed_cell_is_refused() -> None:
    clock = FakeClock()
    cell = make_cell(CellKind.REAL, clock)

    with pytest.raises(SandboxRefusedError, match="borrowed"):
        SandboxCell(cell, FakeSession(_SCRATCH, clock), clock)


def test_a_virtual_cell_is_accepted_and_rooted_below_the_session_scratch() -> None:
    sandbox, _ = _sandbox()
    other, _ = _sandbox()

    assert sandbox.root.parent == _SCRATCH
    assert sandbox.root.name.startswith("comb-")
    assert sandbox.root != other.root
    assert sandbox.cell_id is not None
    assert sandbox.interpreter == sys.executable  # The Warden's own, with the Hive installed.


async def test_put_then_read_round_trips_through_the_session() -> None:
    sandbox, session = _sandbox()

    await sandbox.put(Path("pkg/tool.py"), b"print(1)")

    assert await sandbox.read(Path("pkg/tool.py")) == b"print(1)"
    assert await session.get_file(sandbox.root / "pkg" / "tool.py") == b"print(1)"


async def test_reading_a_missing_file_raises_file_not_found() -> None:
    sandbox, _ = _sandbox()

    with pytest.raises(FileNotFoundError):
        await sandbox.read(Path("nothing.json"))


@pytest.mark.parametrize("relative", ["../escape", "/etc/passwd"])
async def test_put_and_read_refuse_paths_that_leave_the_root(relative: str) -> None:
    sandbox, _ = _sandbox()

    with pytest.raises(SandboxPathError):
        await sandbox.put(Path(relative), b"x")
    with pytest.raises(SandboxPathError):
        await sandbox.read(Path(relative))


async def test_exec_runs_in_the_root_with_the_given_env_and_timeout() -> None:
    seen: list[ExecSpec] = []
    sandbox, _ = _sandbox(seen, _done(stdout=b"out", stderr=b"err", code=7))

    result = await sandbox.exec(("python3", "-V"), 12.0, {"A": "1"})

    assert (result.exit_code, result.stdout, result.stderr) == (7, b"out", b"err")
    assert seen[0].argv == ("python3", "-V")
    assert seen[0].cwd == sandbox.root
    assert seen[0].env == {"A": "1"}
    assert seen[0].timeout_s == 12.0


async def test_exec_caps_the_output_it_keeps() -> None:
    sandbox, _ = _sandbox(outcome=_done(stdout=b"x" * 100), output_bytes=10)

    result = await sandbox.exec(("echo",), 5.0)

    assert result.stdout == b"x" * 10


async def test_a_command_that_times_out_reports_the_timeout_exit_code() -> None:
    clock = FakeClock()
    session = FakeSession(_SCRATCH, clock, slow_commands=frozenset({"sleep"}))
    sandbox = SandboxCell(make_cell(CellKind.VIRTUAL, clock), session, clock)

    result = await sandbox.exec(("sleep", "100"), 1.0)

    assert result.exit_code == TIMED_OUT_EXIT_CODE


async def test_cleanup_removes_the_root_with_rm_and_is_repeatable() -> None:
    seen: list[ExecSpec] = []
    sandbox, _ = _sandbox(seen)

    await sandbox.cleanup()
    await sandbox.cleanup()

    assert seen[0].argv == ("rm", "-rf", str(sandbox.root))
    assert len(seen) == 2


async def test_cleanup_on_a_closed_session_does_nothing() -> None:
    seen: list[ExecSpec] = []
    sandbox, session = _sandbox(seen)
    await session.close()

    await sandbox.cleanup()

    assert seen == []
