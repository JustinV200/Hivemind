"""Unit tests for hivemind.cell.fake.session: FakeSession's scripted exec and in-memory files."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell.errors import CommandTimeoutError, PathNotAllowedError, SessionClosedError
from hivemind.cell.fake import FakeSession
from hivemind.cell.session import (
    CompletedCommand,
    ExecEvent,
    ExecSpec,
    ExitStatus,
    OutputChunk,
    OutputStream,
)
from waggle.clock import FakeClock


async def _drain(session: FakeSession, spec: ExecSpec) -> list[ExecEvent]:
    return [event async for event in session.exec(spec)]


def _exit_status(events: list[ExecEvent]) -> ExitStatus:
    """Narrow the last of `events` to an ExitStatus, as exec's own contract guarantees."""
    final = events[-1]
    assert isinstance(final, ExitStatus)
    return final


async def test_exec_uses_a_callable_responder_and_streams_both_streams_then_exit(
    tmp_path: Path,
) -> None:
    session = FakeSession(
        tmp_path,
        FakeClock(),
        responder=lambda spec: CompletedCommand(
            exit_code=3, stdout=b"out", stderr=b"err", duration_s=1.5
        ),
    )

    events = await _drain(session, ExecSpec(argv=("anything",)))

    assert events[0] == OutputChunk(stream=OutputStream.STDOUT, data=b"out")
    assert events[1] == OutputChunk(stream=OutputStream.STDERR, data=b"err")
    assert _exit_status(events).code == 3
    assert _exit_status(events).duration_s == 1.5


async def test_exec_uses_a_mapping_keyed_by_argv0(tmp_path: Path) -> None:
    scripted = CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)
    session = FakeSession(tmp_path, FakeClock(), responder={"true": scripted})

    events = await _drain(session, ExecSpec(argv=("true",)))

    assert len(events) == 1  # empty stdout/stderr yield no OutputChunks, only the ExitStatus
    assert _exit_status(events).code == 0


async def test_exec_defaults_to_command_not_found_for_an_unscripted_command(
    tmp_path: Path,
) -> None:
    session = FakeSession(tmp_path, FakeClock())

    events = await _drain(session, ExecSpec(argv=("nope",)))

    assert _exit_status(events).code == 127


async def test_exec_of_a_slow_command_raises_command_timeout(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock(), slow_commands=frozenset({"sleep"}))

    with pytest.raises(CommandTimeoutError):
        await _drain(session, ExecSpec(argv=("sleep", "30")))


async def test_put_file_then_get_file_round_trips_bytes(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())

    await session.put_file(Path("nested/data.bin"), b"hello")
    result = await session.get_file(Path("nested/data.bin"))

    assert result == b"hello"


async def test_delete_file_after_close_raises_session_closed(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.put_file(Path("a.txt"), b"x")
    await session.close()

    with pytest.raises(SessionClosedError):
        await session.delete_file(Path("a.txt"))


async def test_get_file_of_an_unwritten_path_raises_file_not_found(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())

    with pytest.raises(FileNotFoundError):
        await session.get_file(Path("never-written.txt"))


async def test_put_file_outside_scratch_raises_unless_allowed(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    session = FakeSession(tmp_path, FakeClock())

    with pytest.raises(PathNotAllowedError):
        await session.put_file(outside, b"data")

    allowing_session = FakeSession(tmp_path, FakeClock(), allowed_paths=(tmp_path.parent,))
    await allowing_session.put_file(outside, b"data")  # Does not raise.


async def test_exec_after_close_raises_session_closed(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.close()

    with pytest.raises(SessionClosedError):
        await _drain(session, ExecSpec(argv=("anything",)))


async def test_close_is_idempotent(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())

    await session.close()
    await session.close()

    assert not session.is_open
