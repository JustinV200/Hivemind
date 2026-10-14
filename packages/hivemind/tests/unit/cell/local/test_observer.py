"""Tests for hivemind.cell.local.observer: the session that looks at a machine and writes nothing.

`ObserverSession` implements the exec clauses of the CellSession contract
(tests/contracts/test_cell_session_contract.py) and deliberately refuses the rest, so it is held to
those clauses here, against real child processes, and to its refusals: a suite that registered it
beside the sessions that write would have to skip most of itself. What matters about it is what it
leaves behind, so the tests also look at the directory it stands in.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/observer.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.observer for the module under test.
    - tests/contracts/test_cell_session_contract.py for the clauses it shares with the others.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hivemind.cell.errors import (
    BackgroundStartError,
    CommandTimeoutError,
    PathNotAllowedError,
    SessionClosedError,
)
from hivemind.cell.local import ObserverSession
from hivemind.cell.local.process import EXIT_COMMAND_NOT_STARTED
from hivemind.cell.session import (
    BackgroundProcess,
    BackgroundSpec,
    CellSession,
    ExecSpec,
    ExitStatus,
    OutputChunk,
    OutputStream,
    run,
)
from waggle.clock import SystemClock

_SHORT_TIMEOUT_S = 0.5  # The one test that really waits for a timeout.
_MISSING_EXECUTABLE = "no-such-executable-8c1e47"


def _session(directory: Path) -> ObserverSession:
    """An observer standing in `directory`, on the real clock (it spawns real children)."""
    return ObserverSession(directory, SystemClock())


def _python(code: str, timeout_s: float = 30.0) -> ExecSpec:
    """A command that runs the Python `code`."""
    return ExecSpec(argv=(sys.executable, "-c", code), timeout_s=timeout_s)


def _names(directory: Path) -> list[str]:
    """The names of what `directory` holds, sorted."""
    return sorted(path.name for path in directory.iterdir())


def _plant(path: Path, content: bytes) -> None:
    """Write `content` to `path`: something of the Warden's own for the observer to leave alone."""
    path.write_bytes(content)


def _same_place(first: str, second: Path) -> bool:
    """Whether the path `first` names the directory `second`, symbolic links settled."""
    return Path(first).resolve() == second.resolve()


def test_an_observer_is_a_cell_session(tmp_path: Path) -> None:
    session: CellSession = _session(tmp_path)  # The protocol is structural: this must type check.

    assert session.is_open and session.scratch_dir == tmp_path


async def test_exec_streams_both_pipes_and_ends_in_exactly_one_exit_status(tmp_path: Path) -> None:
    session = _session(tmp_path)
    code = "import sys; sys.stdout.buffer.write(b'hello'); sys.stderr.buffer.write(b'boom')"

    events = [event async for event in session.exec(_python(code))]

    *chunks, last = events
    assert isinstance(last, ExitStatus) and last.code == 0
    assert all(isinstance(chunk, OutputChunk) for chunk in chunks)
    by_stream = {
        stream: b"".join(
            c.data for c in chunks if isinstance(c, OutputChunk) and c.stream is stream
        )
        for stream in OutputStream
    }
    assert by_stream == {OutputStream.STDOUT: b"hello", OutputStream.STDERR: b"boom"}


async def test_a_command_starts_in_the_directory_it_was_given(tmp_path: Path) -> None:
    session = _session(tmp_path)

    done = await run(session, _python("import os; print(os.getcwd())"))

    assert _same_place(done.stdout.decode().strip(), tmp_path)


async def test_a_nonzero_exit_code_surfaces(tmp_path: Path) -> None:
    done = await run(_session(tmp_path), _python("import sys; sys.exit(7)"))

    assert done.exit_code == 7


async def test_a_command_that_cannot_start_is_a_failed_command_not_an_exception(
    tmp_path: Path,
) -> None:
    done = await run(_session(tmp_path), ExecSpec(argv=(_MISSING_EXECUTABLE,)))

    assert done.exit_code == EXIT_COMMAND_NOT_STARTED


async def test_a_command_past_its_timeout_raises_command_timeout(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(CommandTimeoutError):
        await run(session, _python("import time; time.sleep(60)", timeout_s=_SHORT_TIMEOUT_S))


async def test_a_closed_session_refuses_exec_and_close_is_idempotent(tmp_path: Path) -> None:
    session = _session(tmp_path)

    await session.close()
    await session.close()

    assert not session.is_open
    with pytest.raises(SessionClosedError):
        await run(session, _python("pass"))


async def test_a_command_that_only_reads_leaves_the_directory_as_it_found_it(
    tmp_path: Path,
) -> None:
    _plant(tmp_path / "state.db", b"the warden's own store")
    before = _names(tmp_path)
    session = _session(tmp_path)

    await run(session, _python("import os; os.listdir('.')"))
    await session.close()

    assert _names(tmp_path) == before


async def test_writing_reading_and_deleting_are_refused_for_any_path(tmp_path: Path) -> None:
    session = _session(tmp_path)
    inside, outside = tmp_path / "notes.txt", Path("/etc/hostname")

    for path in (inside, outside):
        with pytest.raises(PathNotAllowedError):
            await session.put_file(path, b"x")
        with pytest.raises(PathNotAllowedError):
            await session.get_file(path)
        with pytest.raises(PathNotAllowedError):
            await session.delete_file(path)
    assert not _names(tmp_path)  # Refused before anything touched the disk.


async def test_nothing_can_be_left_running_and_nothing_is_ever_found_running(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    process = BackgroundProcess(pid=1, argv=("sleep", "1"), log_path=None)

    with pytest.raises(BackgroundStartError):
        await session.start(BackgroundSpec(argv=("sleep", "60")))
    assert not await session.stop(process)
    assert not await session.is_running(process)
