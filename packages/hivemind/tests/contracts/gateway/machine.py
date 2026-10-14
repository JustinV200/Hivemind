"""Look at this machine's disk and process table, where the gateway under test's device is.

The conformance suite runs the gateway on the machine running the suite, so a clause about the
device (a scratch directory is gone after a release, a killed process is not in the process
table) is checked by looking at the machine. These are the few plain questions it asks. They are
synchronous on purpose: the linter forbids filesystem calls inside a coroutine, and a stat or a
process lookup is too small to be worth a thread. `wait_until` is the one bounded wait, for a
state that follows a request by a moment (a process that has not yet been reaped).

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the gateway conformance
    suite beside `contracts.gateway.harness`, which starts the gateway on this machine.

Key invariants:
    - Every wait is bounded (`WAIT_S`) and a timeout fails the test naming what it waited for.
    - A zombie is not alive: a process that has exited and not yet been reaped is gone.

See Also:
    - docs/waggle/gateway-contract.md sections 10 and 11 for the clauses these answer.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psutil

POLL_S = 0.05  # How often a wait on the device's disk or process table looks again.
WAIT_S = 10.0  # The longest such a wait lasts.

__all__ = [
    "alive",
    "exists",
    "is_directory",
    "names_in",
    "python_argv",
    "read_file",
    "same_path",
    "wait_until",
    "write_file",
]


def python_argv(script: str, *args: str) -> list[str]:
    """The argument list that runs a Python one-liner with the interpreter running the suite."""
    return [sys.executable, "-c", script, *args]


def names_in(path: str) -> list[str]:
    """The names in a directory on this machine, sorted."""
    return sorted(os.listdir(path))


def same_path(left: str | Path, right: str | Path) -> bool:
    """Whether two spellings name one path on this machine, symbolic links resolved."""
    return os.path.realpath(left) == os.path.realpath(right)


def exists(path: str) -> bool:
    """Whether a path exists on this machine (the gateway's device is this machine)."""
    return os.path.exists(path)


def is_directory(path: str) -> bool:
    """Whether a path is a directory on this machine."""
    return os.path.isdir(path)


def write_file(path: Path, data: bytes) -> None:
    """Write a file on this machine, for a test to set up what the gateway will see."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_file(path: Path) -> bytes:
    """Read a file on this machine, for a test to see what the gateway left."""
    return path.read_bytes()


def alive(pid: int) -> bool:
    """Whether a process is running on this machine: a zombie, already dead, is not."""
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def wait_until(condition: Callable[[], bool], what: str, timeout_s: float = WAIT_S) -> None:
    """Poll `condition` on a short interval until it holds, or fail naming `what`."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(POLL_S)
