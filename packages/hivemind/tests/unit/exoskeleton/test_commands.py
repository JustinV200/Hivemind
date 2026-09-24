"""Unit tests for hivemind.exoskeleton.commands: run_peripheral and sanitised."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import CompletedCommand, ExecSpec
from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.commands import (
    MAX_STDERR_CHARS,
    PeripheralCommand,
    run_peripheral,
    sanitised,
)
from hivemind.exoskeleton.errors import PeripheralError
from waggle.clock import FakeClock


def _session(tmp_path: Path, completed: CompletedCommand, seen: list[ExecSpec]) -> FakeSession:
    def respond(spec: ExecSpec) -> CompletedCommand:
        seen.append(spec)
        return completed

    return FakeSession(tmp_path, FakeClock(), responder=respond)


def _done(code: int, stdout: bytes = b"", stderr: bytes = b"") -> CompletedCommand:
    return CompletedCommand(exit_code=code, stdout=stdout, stderr=stderr, duration_s=0.0)


async def test_success_returns_stdout_and_passes_env_and_timeout(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []
    session = _session(tmp_path, _done(0, stdout=b"image"), seen)
    command = PeripheralCommand(argv=("tool", "-x"), timeout_s=3.0, env={"DISPLAY": ":1"})

    output = await run_peripheral(session, command, "compound_eye", "capture")

    assert output == b"image"
    assert seen[0].argv == ("tool", "-x")
    assert seen[0].env == {"DISPLAY": ":1"}
    assert seen[0].timeout_s == 3.0


async def test_a_listed_ok_code_counts_as_success(tmp_path: Path) -> None:
    session = _session(tmp_path, _done(124, stdout=b"pcm"), [])
    command = PeripheralCommand(argv=("timeout",), timeout_s=1.0, ok_codes=(0, 124))

    assert await run_peripheral(session, command, "buzz", "listen") == b"pcm"


async def test_a_failure_names_the_tool_and_quotes_stderr_but_never_stdout_or_arguments(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path, _done(2, stdout=b"SECRET-OUT", stderr=b"bad\x07thing"), [])
    command = PeripheralCommand(argv=("xdotool", "type", "--", "hunter2"), timeout_s=1.0)

    with pytest.raises(PeripheralError) as raised:
        await run_peripheral(session, command, "antennae", "type")

    message = str(raised.value)
    assert "xdotool exited 2: bad thing" in message
    assert "SECRET-OUT" not in message
    assert "hunter2" not in message
    assert (raised.value.peripheral, raised.value.operation) == ("antennae", "type")


async def test_a_timeout_becomes_a_peripheral_error(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock(), slow_commands=frozenset({"import"}))
    command = PeripheralCommand(argv=("import", "png:-"), timeout_s=10.0)

    with pytest.raises(PeripheralError, match=r"took longer than 10\.0s"):
        await run_peripheral(session, command, "compound_eye", "capture")


async def test_a_closed_session_becomes_a_peripheral_error(tmp_path: Path) -> None:
    session = _session(tmp_path, _done(0), [])
    await session.close()

    with pytest.raises(PeripheralError, match="session is closed"):
        await run_peripheral(session, PeripheralCommand(("x",), 1.0), "buzz", "listen")


def test_sanitised_keeps_the_last_characters_on_one_printable_line() -> None:
    text = sanitised(b"a" * 1000 + b"\nthe real reason\x1b[0m")

    assert len(text) == MAX_STDERR_CHARS
    assert text.endswith("the real reason [0m")
    assert "\n" not in text
