"""Run one peripheral command on a Cell and turn every way it can fail into a PeripheralError.

Every X11 and PulseAudio backend of the Exoskeleton (the optional display, input, audio and
browser attachment of a Cell) is a thin adapter: build an argument list, run it on the Cell through
its `CellSession`, read stdout. The failure handling is identical for all of them and easy to get
subtly wrong, so it lives here once: a non-zero exit (outside the ones a caller says are success),
a timeout and a closed session all become `PeripheralError`s that name the peripheral and the
operation and quote at most a short, sanitised slice of stderr, never stdout (which may be a
screenshot or audio) and never the command's arguments (which may be typed text).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`.
    Called by `compound_eye.x11`, `antennae.xdotool`, `buzz.pulseaudio` and the attach helpers
    that start and probe a display or a sound server. Calls into `hivemind.cell` (CellSession,
    ExecSpec, run and its two errors) and `hivemind.exoskeleton.errors` only.

Key invariants:
    - An error message carries the program name and at most MAX_STDERR_CHARS of stderr, with
      control characters replaced; never stdout and never any argument after the program.
    - The command always runs with its own timeout; nothing here waits without a bound.

See Also:
    - hivemind.cell.session for CellSession.exec and run.
    - hivemind.exoskeleton.errors for PeripheralError.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from hivemind.cell import CellSession, CommandTimeoutError, ExecSpec, SessionClosedError, run
from hivemind.exoskeleton.errors import PeripheralError

MAX_STDERR_CHARS = 240  # Enough to name what went wrong; never enough to carry a payload.

__all__ = ["MAX_STDERR_CHARS", "PeripheralCommand", "run_peripheral", "sanitised"]


@dataclass(frozen=True, slots=True)
class PeripheralCommand:
    """One command a peripheral runs on its Cell, and how to judge it.

    Attributes:
        argv: The program and its arguments; never run through a shell.
        timeout_s: Seconds before the session kills it; every peripheral command has one.
        env: Extra environment (DISPLAY, XAUTHORITY, a sound server) the command needs.
        ok_codes: Exit codes that mean success; `timeout` exits 124 when it stops a recorder
            on schedule, which the Buzz backend counts as success.
    """

    argv: tuple[str, ...]
    timeout_s: float
    env: Mapping[str, str] = field(default_factory=dict)
    ok_codes: Collection[int] = (0,)


async def run_peripheral(
    session: CellSession, command: PeripheralCommand, peripheral: str, operation: str
) -> bytes:
    """Run `command` on the Cell and return its stdout, or raise a PeripheralError.

    Args:
        session: The Cell's session.
        command: What to run, its timeout, environment and success codes.
        peripheral: Which peripheral is running it, for the error ("compound_eye", ...).
        operation: What it is doing, for the error ("capture", "click", ...).

    Returns:
        Everything the command wrote to stdout.

    Raises:
        PeripheralError: It exited with a code outside `command.ok_codes`, timed out, or the
            session was already closed.
    """
    spec = ExecSpec(argv=command.argv, env=dict(command.env), timeout_s=command.timeout_s)
    try:
        # One short command on the Cell; its own ExecSpec timeout bounds the wait, and a timeout
        # surfaces as CommandTimeoutError, turned into the peripheral's own error below.
        completed = await run(session, spec)
    except CommandTimeoutError as error:
        raise PeripheralError(
            peripheral, operation, f"{command.argv[0]} took longer than {command.timeout_s}s"
        ) from error
    except SessionClosedError as error:
        raise PeripheralError(peripheral, operation, "the Cell's session is closed") from error
    if completed.exit_code not in command.ok_codes:
        detail = sanitised(completed.stderr)
        raise PeripheralError(
            peripheral,
            operation,
            f"{command.argv[0]} exited {completed.exit_code}" + (f": {detail}" if detail else ""),
        )
    return completed.stdout


def sanitised(output: bytes) -> str:
    """Return at most the last MAX_STDERR_CHARS of a tool's diagnostics as one printable line.

    The tail, not the head: a tool's last words say why it stopped. For stderr and for the logs
    of processes attach started, never for stdout, which may be an image or audio.

    Args:
        output: The raw diagnostic bytes.

    Returns:
        One line, control characters replaced by spaces.
    """
    text = output.decode("utf-8", errors="replace").strip()
    printable = "".join(char if char.isprintable() else " " for char in text)
    return printable[-MAX_STDERR_CHARS:]
