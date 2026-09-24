"""Start, stop and reap long-running background processes for a CellSession: the BackgroundTable.

A Cell's terminal session runs a command to completion with `exec`; a display server, a sound
server or a browser must instead keep running after the call that started it returns (roadmap
step 6.4, ADR-0031). `BackgroundTable` is that machinery, shared by `hivemind.cell.local.session.
LocalProcessSession` (the Hive Stand) and `hivemind.cell.in_cell.InCellSession` (code running
inside its own Virtual Cell) exactly as `hivemind.cell.local.process` shares `exec`'s: spawn the
command in its own process group (a new session on POSIX, a new process group on Windows) with
stdin closed and stdout and stderr going to one log file in scratch or to nowhere, report the pid
to the caller's lease before returning, and keep the `asyncio` process handle so the process can
be stopped and reaped. Stopping kills the whole group with `kill_process_tree` (the same
SIGTERM-then-SIGKILL, or `taskkill /T`, a lease's release uses) and then waits for the child, so
no process is ever left for the event loop to find unreaped.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local`. Owned by one
    `LocalProcessSession` or `InCellSession` each, which delegate `start`, `stop`, `is_running`
    and the background half of `close` to it. Calls into the standard library (`asyncio`, `os`,
    `subprocess`), `hivemind.cell.errors`, `hivemind.cell.session`, `hivemind.cell.local.process`
    (`resolve_cwd`) and `hivemind.cell.local.releaser` (`kill_process_tree`) only.

Key invariants:
    - `start` calls `ctx.on_started(pid)` before it returns and before anything else touches the
      child, so a crash right after a start still leaves the pid where a release will kill it.
    - A log file always resolves inside scratch: `resolve_scratch_path` with no allowed paths.
    - Every process this table started is either still in the table or has been reaped: `stop`
      and `stop_all` await the child after killing it (bounded by `STOP_WAIT_S`), so the event
      loop never closes over a running or unreaped child.
    - `stop` kills the process group even when its leader has already exited, since a daemon the
      leader forked can outlive it in the same group.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why a session starts
      background processes at all.
    - hivemind.cell.session for BackgroundSpec, BackgroundProcess and the CellSession contract.
    - hivemind.cell.local.releaser for kill_process_tree.
    - hivemind.cell.local.process for exec's own spawn machinery, which this module mirrors.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from hivemind.cell.errors import BackgroundStartError
from hivemind.cell.local.process import resolve_cwd
from hivemind.cell.local.releaser import kill_process_tree
from hivemind.cell.session import BackgroundProcess, BackgroundSpec, resolve_scratch_path
from hivemind.common.logging import get_logger
from waggle.clock import Clock

STOP_WAIT_S = 5.0  # How long to wait for a killed child to be reaped before logging and moving on.

__all__ = ["STOP_WAIT_S", "BackgroundContext", "BackgroundTable"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BackgroundContext:
    """What `BackgroundTable.start` needs beyond the spec itself (codingrules 5.1).

    Attributes:
        scratch_dir: The session's scratch directory: the default working directory, and the
            only place a log file may be written.
        on_started: Called once, with the new pid, the moment it is known; a session passes its
            lease's `note_started_process` so the lease's release can kill the process too.
    """

    scratch_dir: Path
    on_started: Callable[[int], None]


class BackgroundTable:
    """Every background process one session started and has not yet reaped.

    Owns mutable state (codingrules 8.5, documented): `_running` maps a pid to the `asyncio`
    process handle `start` created, and shrinks as `stop` and `stop_all` reap each one.
    """

    def __init__(self, clock: Clock) -> None:
        """Build an empty table.

        Args:
            clock: Source of the SIGTERM-to-SIGKILL grace period `kill_process_tree` waits.
        """
        self._clock = clock
        self._running: dict[int, asyncio.subprocess.Process] = {}

    async def start(self, spec: BackgroundSpec, ctx: BackgroundContext) -> BackgroundProcess:
        """Start `spec` in its own process group and return once its pid is recorded.

        Args:
            spec: The command to start.
            ctx: The session's scratch directory and started-pid callback.

        Returns:
            The started process.

        Raises:
            PathNotAllowedError: `spec.log_path` resolves outside scratch.
            BackgroundStartError: The OS could not start the command at all.
        """
        log_path = (
            resolve_scratch_path(ctx.scratch_dir, spec.log_path)
            if spec.log_path is not None
            else None
        )
        # Opening the log is filesystem I/O, so it runs off the event loop (codingrules 11).
        log_file = await asyncio.to_thread(_open_log, log_path) if log_path is not None else None
        try:
            process = await _spawn(spec, resolve_cwd(ctx.scratch_dir, spec.cwd), log_file)
        except OSError as error:
            # Infrastructure asked for this process and must know it is not running, unlike exec,
            # whose unstartable command is an ordinary failed command a bee can read.
            raise BackgroundStartError(spec.argv, error.strerror or str(error)) from error
        finally:
            # The child holds its own copy of the descriptor; the parent's copy is not needed.
            if log_file is not None:
                log_file.close()
        # Recorded before anything else happens, so a crash here still leaves the pid for the
        # lease's release to find (this module's first invariant).
        ctx.on_started(process.pid)
        self._running[process.pid] = process
        log.debug("cell.background_started", pid=process.pid, program=spec.argv[0])
        return BackgroundProcess(pid=process.pid, argv=spec.argv, log_path=log_path)

    async def stop(self, process: BackgroundProcess) -> bool:
        """Kill `process`'s whole group and reap it.

        Args:
            process: A process this table's own `start` returned.

        Returns:
            True when its leader was still running; False when it had exited, was already
            stopped, or was never started here.
        """
        return await self._stop_pid(process.pid)

    def is_running(self, process: BackgroundProcess) -> bool:
        """Return whether `process`, started by this table, is still running.

        Args:
            process: A process this table's own `start` returned.

        Returns:
            True while its leader runs; False once it has exited or been stopped, or when it was
            never started here.
        """
        handle = self._running.get(process.pid)
        return handle is not None and handle.returncode is None

    async def stop_all(self) -> int:
        """Stop every process this table still holds, newest first.

        Returns:
            How many of them were still running when stopped.
        """
        stopped = 0
        # Newest first: a process started later (a window manager) usually depends on one started
        # earlier (its display), so it goes first and never sees its dependency vanish under it.
        for pid in reversed(list(self._running)):
            if await self._stop_pid(pid):
                stopped += 1
        return stopped

    async def _stop_pid(self, pid: int) -> bool:
        """Kill `pid`'s whole group and reap its handle; False when it is not in this table."""
        handle = self._running.pop(pid, None)
        if handle is None:
            return False  # Already stopped, or never ours: stop is idempotent by contract.
        was_running = handle.returncode is None
        # The group is killed even when the leader has exited: a daemon it forked can outlive it.
        await kill_process_tree(pid, self._clock)
        await _reap(handle)
        return was_running


# ──────────────────────────────────────────────────────────────────────────────
# Spawning and reaping
# ──────────────────────────────────────────────────────────────────────────────


async def _spawn(
    spec: BackgroundSpec, cwd: Path, log_file: IO[bytes] | None
) -> asyncio.subprocess.Process:
    """Start `spec` detached from this process's own group, POSIX and Windows side by side."""
    output: IO[bytes] | int = log_file if log_file is not None else asyncio.subprocess.DEVNULL
    env = {**os.environ, **spec.env}
    if sys.platform == "win32":
        return await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        return await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )


async def _reap(handle: asyncio.subprocess.Process) -> None:
    """Wait for a killed child to be reaped, bounded by STOP_WAIT_S; log and move on if not."""
    try:
        # An external await on the OS: normally immediate after the kill, since SIGKILL cannot
        # be ignored; the bound only guards a child stuck in uninterruptible I/O.
        async with asyncio.timeout(STOP_WAIT_S):
            await handle.wait()
    except TimeoutError:
        log.warning("cell.background_unreaped", pid=handle.pid, waited_s=STOP_WAIT_S)


def _open_log(path: Path) -> IO[bytes]:
    """Create `path`'s parent directories and open it for appending bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("ab")
