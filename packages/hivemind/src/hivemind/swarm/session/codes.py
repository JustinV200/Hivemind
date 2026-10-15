"""Provide the code tables of a device session: errors a device sends, and how an exec ended.

A `CellSession` promises a small set of errors (a closed session, a path out of reach, a missing
file, a command that missed its deadline) that its callers already handle, whichever kind of Cell
they are driving. A Pollen Packet reports the same facts as a `control.error` with a stable code
(`pollen.not_found`, `pollen.path_refused`, `hive.session.lease_not_open`, ...; gateway contract
section 15), so `translate` turns the codes the contract names into those promised errors and
leaves every other code as the `DeviceRequestError` that carries it. The second table here is
how a `session.exit` becomes the `code` of an `ExitStatus`: a process that exited keeps its code,
one ended by a signal gets the negative of the signal's number (the convention of the local
session, whose `code` is Python's `returncode`), and one the device killed gets `KILLED_EXIT_CODE`
with the device's reason surfaced by the caller as a line on standard error.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Used by the session's exec, file and process operations. Calls into hivemind.cell and
    hivemind.swarm.errors, and waggle for the exit message.

Key invariants:
    - `translate` never loses information: an unmapped code comes back as the same
      `DeviceRequestError`, and a mapped one is raised `from` it by the caller.
    - No message here repeats a path the device did not name to us: the path in a
      `PathNotAllowedError` is the one the caller itself asked for.
    - A signal every POSIX system numbers alike (`SIGKILL`, `SIGTERM`, ...) gets that number on
      any Hive Stand, a Windows one included, whose own table has no `SIGKILL` and puts `SIGABRT`
      at 22. Any other name is looked up on this host, and one it has no number for gets
      `UNKNOWN_SIGNAL_EXIT_CODE`, never an exception: a Windows device's `CTRL_BREAK_EVENT` is a
      valid answer on a Linux Hive.

See Also:
    - docs/waggle/gateway-contract.md sections 11 and 15 for the exit outcomes and the codes.
    - hivemind.cell.errors for the errors a CellSession promises.
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from hivemind.cell.errors import BackgroundStartError, PathNotAllowedError, SessionClosedError
from hivemind.swarm.errors import CommandDeniedError, DeviceRequestError
from waggle.messages.session.commands import SessionOutcome
from waggle.messages.session.output import SessionExit

CAPABILITY_DENIED = "pollen.capability_denied"
PATH_REFUSED = "pollen.path_refused"
NOT_FOUND = "pollen.not_found"
START_FAILED = "pollen.start_failed"
LEASE_NOT_OPEN = "hive.session.lease_not_open"

KILLED_EXIT_CODE = -9  # The device killed it: the SIGKILL convention a local session reports.
UNKNOWN_SIGNAL_EXIT_CODE = -1  # A signal this host has no number for (a Windows control event).

# The numbers Linux, macOS and the BSDs all give these signals. A device reports a signal by name,
# and the number must be the device's, not the host's: these are, wherever the Hive Stand runs.
_POSIX_SIGNALS = MappingProxyType(
    {
        "SIGHUP": 1,
        "SIGINT": 2,
        "SIGQUIT": 3,
        "SIGILL": 4,
        "SIGTRAP": 5,
        "SIGABRT": 6,
        "SIGFPE": 8,
        "SIGKILL": 9,
        "SIGSEGV": 11,
        "SIGPIPE": 13,
        "SIGALRM": 14,
        "SIGTERM": 15,
    }
)

__all__ = [
    "CAPABILITY_DENIED",
    "KILLED_EXIT_CODE",
    "LEASE_NOT_OPEN",
    "NOT_FOUND",
    "PATH_REFUSED",
    "START_FAILED",
    "UNKNOWN_SIGNAL_EXIT_CODE",
    "ErrorContext",
    "exit_code_for",
    "translate",
]


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """What a translated error should say about the request that failed.

    Attributes:
        scratch_dir: The session's scratch directory, which a closed-session error names.
        lease_id: The lease the request ran in.
        path: The device path the request named, for a path error; None when it named none.
        argv: The command a background start asked for, for a start error.
        needed: The capability the request needed, for a denial.
    """

    scratch_dir: Path
    lease_id: str
    path: str | None = None
    argv: tuple[str, ...] = ()
    needed: str = ""


Factory = Callable[[DeviceRequestError, ErrorContext], Exception]


def _denied(error: DeviceRequestError, context: ErrorContext) -> Exception:
    """The device's own capability wall refused what the Hive's Guard let through."""
    return CommandDeniedError(context.lease_id, context.needed or "an unnamed capability")


def _path_refused(error: DeviceRequestError, context: ErrorContext) -> Exception:
    """The device resolved the path (links included) and found it out of reach."""
    return PathNotAllowedError(Path(context.path or ""))


def _not_found(error: DeviceRequestError, context: ErrorContext) -> Exception:
    """The file the request named is not there."""
    return FileNotFoundError(f"The device has no such file for the request on {context.lease_id}.")


def _start_failed(error: DeviceRequestError, context: ErrorContext) -> Exception:
    """The program could not be started in the background."""
    return BackgroundStartError(context.argv or ("",), error.device_message)


def _lease_not_open(error: DeviceRequestError, context: ErrorContext) -> Exception:
    """The device no longer holds the lease open, so the session is over."""
    return SessionClosedError(context.scratch_dir)


# One entry per code the contract promises a CellSession error for. A code not listed here keeps
# its DeviceRequestError, so a new device code is visible rather than silently mapped.
_FACTORIES: dict[str, Factory] = {
    CAPABILITY_DENIED: _denied,
    PATH_REFUSED: _path_refused,
    NOT_FOUND: _not_found,
    START_FAILED: _start_failed,
    LEASE_NOT_OPEN: _lease_not_open,
}


def translate(error: DeviceRequestError, context: ErrorContext) -> Exception:
    """Turn a device's refusal into the error a `CellSession` caller expects.

    Args:
        error: The refusal, carrying the device's code.
        context: What the failed request was, so the error can name it.

    Returns:
        The `hivemind.cell` or `hivemind.swarm` error the code maps to, or `error` itself when the
        code has no promised mapping. The caller raises it `from error`.
    """
    factory = _FACTORIES.get(error.device_code)
    return error if factory is None else factory(error, context)


def exit_code_for(exit_message: SessionExit) -> int:
    """Return the `ExitStatus.code` a finished exec's `session.exit` stands for.

    Args:
        exit_message: The exit of an EXEC request whose outcome is not TIMED_OUT (a timeout is
            raised by the caller, not given a code).

    Returns:
        The process's exit code when it exited, the negative signal number when a signal ended
        it, and `KILLED_EXIT_CODE` when the device killed it.
    """
    if exit_message.outcome is SessionOutcome.EXITED:
        return exit_message.exit_code or 0
    if exit_message.outcome is SessionOutcome.SIGNALED:
        return _signal_code(exit_message.signal_name)
    return KILLED_EXIT_CODE


def _signal_code(name: str | None) -> int:
    """Return `-n` for the signal called `name`, or `UNKNOWN_SIGNAL_EXIT_CODE`.

    The number is POSIX's own where every POSIX system agrees on it, else this host's.
    """
    portable = _POSIX_SIGNALS.get(name or "")
    if portable is not None:
        return -portable
    known = signal.Signals.__members__.get(name or "")
    return UNKNOWN_SIGNAL_EXIT_CODE if known is None else -int(known)
