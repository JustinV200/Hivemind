"""Contract suite for Sandbox: one set of behaviours, run over every implementation.

Fits into the Hive:
    Test infrastructure, not shipped. Each test states one clause of
    hivemind.royal_jelly.quarantine_comb.sandbox.Sandbox's contract and runs it against
    `SandboxSubprocess` (a temp directory and the local interpreter) and `SandboxCell` over a real
    `InCellSession` on a temp lease, so the development sandbox is proven to behave exactly like
    the production one before the Comb relies on either (codingrules 14.3). Everything runs with
    the local interpreter; nothing here needs a container.

Key invariants:
    - Every implementation gives the same answers for the same calls: round-trips, cwd, env,
      output cap, timeout exit code, path refusal and cleanup.

See Also:
    - hivemind.royal_jelly.quarantine_comb.sandbox for the contract under test.
    - tests/contracts/test_cell_session_contract.py for the session suite this one builds on.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from builders.cells import make_cell, make_real_cell_lease

from hivemind.cell.in_cell import InCellLeaseReleaser, InCellSession
from hivemind.cell.models import CellKind
from hivemind.royal_jelly.quarantine_comb import (
    Sandbox,
    SandboxCell,
    SandboxPathError,
    SandboxSubprocess,
)
from hivemind.royal_jelly.quarantine_comb.sandbox import DEFAULT_OUTPUT_BYTES, TIMED_OUT_EXIT_CODE
from waggle.clock import SystemClock

SandboxFactory = Callable[..., Sandbox]
_KINDS = ("subprocess", "cell_in_cell")


@pytest.fixture(params=_KINDS)
async def make_sandbox(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[SandboxFactory]:
    """Yield a factory for the parametrised implementation; every sandbox it made is cleaned."""
    made: list[Sandbox] = []

    def build(output_bytes: int = DEFAULT_OUTPUT_BYTES) -> Sandbox:
        clock = SystemClock()
        sandbox: Sandbox
        if request.param == "subprocess":
            sandbox = SandboxSubprocess(clock, output_bytes=output_bytes)
        else:
            # A real clock: the session's timeout watchdog measures real elapsed time.
            scratch = tmp_path / "scratch"
            scratch.mkdir(exist_ok=True)
            lease = make_real_cell_lease(scratch, clock=clock, releaser=InCellLeaseReleaser(clock))
            session = InCellSession(lease, clock)
            cell = make_cell(CellKind.VIRTUAL, clock)
            sandbox = SandboxCell(
                cell, session, clock, output_bytes=output_bytes, interpreter=sys.executable
            )
        made.append(sandbox)
        return sandbox

    yield build
    for sandbox in made:
        await sandbox.cleanup()


def _same_path(printed: str, root: Path) -> bool:
    """Whether a path a child printed names the sandbox root, symlinks resolved on both sides."""
    return os.path.realpath(printed) == os.path.realpath(root)


async def test_a_put_file_reads_back_byte_for_byte(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox()
    data = bytes(range(256)) * 4

    await sandbox.put(Path("blob.bin"), data)

    assert await sandbox.read(Path("blob.bin")) == data


async def test_put_creates_parent_directories_and_overwrites(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox()

    await sandbox.put(Path("a/b/c.txt"), b"one")
    await sandbox.put(Path("a/b/c.txt"), b"two")

    assert await sandbox.read(Path("a/b/c.txt")) == b"two"
    assert (sandbox.root / "a" / "b" / "c.txt").is_file()


async def test_reading_a_missing_file_raises_file_not_found(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox()

    with pytest.raises(FileNotFoundError):
        await sandbox.read(Path("missing.json"))


@pytest.mark.parametrize("relative", ["../escape.txt", "a/../../escape.txt", "/etc/passwd", ""])
async def test_paths_that_leave_the_root_are_refused(
    make_sandbox: SandboxFactory, relative: str
) -> None:
    sandbox = make_sandbox()

    with pytest.raises(SandboxPathError):
        await sandbox.put(Path(relative), b"x")
    with pytest.raises(SandboxPathError):
        await sandbox.read(Path(relative))


async def test_exec_runs_in_the_root_and_sees_files_put_there(
    make_sandbox: SandboxFactory,
) -> None:
    sandbox = make_sandbox()
    await sandbox.put(Path("input.txt"), b"hello")
    script = "import os; print(os.getcwd()); print(open('input.txt').read())"

    result = await sandbox.exec((sandbox.interpreter, "-c", script), 30.0)

    lines = result.stdout.decode().splitlines()
    assert result.exit_code == 0
    assert _same_path(lines[0], sandbox.root)
    assert lines[1] == "hello"


async def test_exec_reports_exit_code_and_both_streams(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox()
    script = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"

    result = await sandbox.exec((sandbox.interpreter, "-c", script), 30.0)

    assert (result.exit_code, result.stdout.strip(), result.stderr.strip()) == (3, b"out", b"err")
    assert result.duration_s >= 0


async def test_exec_passes_the_extra_environment(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox()
    script = "import os; print(os.environ['COMB_CONTRACT'])"

    result = await sandbox.exec((sandbox.interpreter, "-c", script), 30.0, {"COMB_CONTRACT": "yes"})

    assert result.stdout.strip() == b"yes"


async def test_a_command_past_its_timeout_reports_the_timeout_exit_code_quickly(
    make_sandbox: SandboxFactory,
) -> None:
    sandbox = make_sandbox()
    script = "import time; print('started', flush=True); time.sleep(60)"

    result = await sandbox.exec((sandbox.interpreter, "-c", script), 1.0)

    assert result.exit_code == TIMED_OUT_EXIT_CODE
    assert result.duration_s < 30
    assert result.stdout.startswith(b"started")


async def test_captured_output_is_capped(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox(output_bytes=64)
    script = "import sys; sys.stdout.write('x' * 100000); sys.stderr.write('y' * 100000)"

    result = await sandbox.exec((sandbox.interpreter, "-c", script), 30.0)

    assert result.exit_code == 0
    assert 0 < len(result.stdout) + len(result.stderr) <= 64


async def test_cleanup_removes_the_root_and_can_be_repeated(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox()
    await sandbox.put(Path("x/y.txt"), b"1")

    await sandbox.cleanup()
    await sandbox.cleanup()

    assert not sandbox.root.exists()


async def test_the_sandbox_reports_whether_it_is_a_cell(make_sandbox: SandboxFactory) -> None:
    sandbox = make_sandbox()

    # SandboxSubprocess has no Cell; SandboxCell reports the id of the Cell it wraps.
    assert (sandbox.cell_id is None) == isinstance(sandbox, SandboxSubprocess)
