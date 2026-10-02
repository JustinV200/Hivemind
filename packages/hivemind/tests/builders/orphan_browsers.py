"""Find and stop the Chromium processes a killed earlier test run left behind.

A clean detach stops every browser a lease started, but after a `kill -9` or a hard timeout of
pytest itself, one can survive (phase 6 handoff 4.5 item 7) and keep its memory and its DevTools
port until someone runs `pkill`. The test session's start (`conftest.pytest_sessionstart`) calls
`stop_orphaned_test_browsers` to stop those. A process qualifies only when all three hold: its
executable is a Chromium-family binary, one of its arguments names a real browser harness's
scratch directory (`/tmp/hm-...`, the prefix `contracts.browser_harness` and
`contracts.exoskeleton_harness` create), and no Python process is among its ancestors. The last
is what keeps a concurrent test run safe: its browsers descend from its own live pytest, while
an orphan's parent died and the kernel handed it to init. It reads Linux's `/proc` only, the one
platform the real harnesses run on; elsewhere it finds nothing, without spawning a process.

Fits into the Hive:
    Test infrastructure (codingrules section 14), not shipped. Called by the hivemind test
    suite's own `conftest.py`. Calls into the standard library only.

Key invariants:
    - Never signals a process with a live Python ancestor, nor one whose arguments name no
      harness scratch directory, nor one whose executable is not Chromium-family.
    - Finds nothing, and signals nothing, where `/proc` does not exist (Windows, macOS).

See Also:
    - contracts.browser_harness for the real harness whose browsers this cleans up after.
    - hivemind.exoskeleton.browser.launch for the arguments a lease-started browser is given.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROC_ROOT = Path("/proc")  # Linux's process table; absent on Windows and macOS.
# Every real browser harness makes its scratch with tempfile.mkdtemp(prefix="hm-", dir="/tmp"),
# and every path a lease-started browser is given (its profile, its crash database) is under it.
HARNESS_SCRATCH_MARKER = "/tmp/hm-"  # noqa: S108  # SAFETY: matched in command lines, never created.
# Executable names a Chromium-family browser and its helpers run under: the browser itself (and
# its renderer, GPU and utility children, which share its binary) and the crash handler.
BROWSER_NAMES = frozenset(
    {"chrome", "chromium", "chromium-browser", "chrome_crashpad_handler", "headless_shell"}
)
_MAX_ANCESTORS = 64  # A process tree deeper than this is a loop in a racing read; stop walking.
_ZOMBIE = "Z"  # A zombie has already exited; there is nothing left to stop.
# SIGKILL, not SIGTERM: an orphan has nobody left to answer its graceful shutdown. Windows has no
# SIGKILL, but it has no /proc either, so nothing is ever found there to send this to.
KILL_SIGNAL = int(getattr(signal, "SIGKILL", signal.SIGTERM))

__all__ = [
    "BROWSER_NAMES",
    "HARNESS_SCRATCH_MARKER",
    "KILL_SIGNAL",
    "PROC_ROOT",
    "orphaned_test_browsers",
    "stop_orphaned_test_browsers",
]


@dataclass(frozen=True, slots=True)
class _Process:
    """One live process as `/proc` describes it: its parent and its command line."""

    ppid: int  # Its parent's pid; 0 for the kernel's own.
    argv: tuple[str, ...]  # Its command line, split on NUL; empty for a kernel thread.


def orphaned_test_browsers(proc_root: Path = PROC_ROOT) -> tuple[int, ...]:
    """Return the pids of every orphaned, harness-started browser process under `proc_root`.

    Args:
        proc_root: The process table to read; a test passes a fake one.

    Returns:
        The qualifying pids (module docstring), in ascending order; empty without `proc_root`.
    """
    processes = _read_processes(proc_root)
    return tuple(
        sorted(
            pid
            for pid, process in processes.items()
            if _is_harness_browser(process) and not _has_python_ancestor(process, processes)
        )
    )


def stop_orphaned_test_browsers(
    proc_root: Path = PROC_ROOT, kill: Callable[[int, int], None] = os.kill
) -> tuple[int, ...]:
    """Kill every orphaned, harness-started browser process, and return their pids.

    Args:
        proc_root: The process table to read; a test passes a fake one.
        kill: Sends the signal; `os.kill` unless a test records the calls instead.

    Returns:
        The pids signalled; one that exited first or is not ours is passed over silently.
    """
    pids = orphaned_test_browsers(proc_root)
    # One signal per orphan; the kernel delivers SIGKILL at once, so nothing is awaited.
    for pid in pids:
        try:
            kill(pid, KILL_SIGNAL)
        except (ProcessLookupError, PermissionError):
            continue  # It exited since it was read, or another user's: either way, not ours.
    return pids


def _read_processes(proc_root: Path) -> dict[int, _Process]:
    """Read every live, non-zombie process under `proc_root`, keyed by pid."""
    if not proc_root.is_dir():
        return {}
    processes: dict[int, _Process] = {}
    # One entry per numeric directory; a process can exit mid-read, which skips it.
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            # "pid (comm) state ppid ...": comm may hold spaces or ")", so split after the last.
            fields = (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            state, ppid = fields[0], int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
        if state == _ZOMBIE:
            continue
        argv = tuple(part.decode(errors="replace") for part in raw.split(b"\0") if part)
        processes[int(entry.name)] = _Process(ppid=ppid, argv=argv)
    return processes


def _is_harness_browser(process: _Process) -> bool:
    """Return whether `process` is a Chromium-family binary given a harness scratch path."""
    if not process.argv or Path(process.argv[0]).name not in BROWSER_NAMES:
        return False
    return any(HARNESS_SCRATCH_MARKER in argument for argument in process.argv[1:])


def _has_python_ancestor(process: _Process, processes: dict[int, _Process]) -> bool:
    """Return whether any ancestor of `process` runs a Python interpreter (a live test run)."""
    pid = process.ppid
    # Walk parent links up to init (or a subreaper's own parent, init again) or the kernel.
    for _ in range(_MAX_ANCESTORS):
        if pid <= 1:
            return False  # Reached init without meeting Python: the run that owned it is gone.
        ancestor = processes.get(pid)
        if ancestor is None:
            return True  # A parent this read cannot see: leave it alone rather than guess.
        if ancestor.argv and Path(ancestor.argv[0]).name.startswith("python"):
            return True
        pid = ancestor.ppid
    return True  # Too deep to tell: leave it alone rather than risk a live run's browser.
