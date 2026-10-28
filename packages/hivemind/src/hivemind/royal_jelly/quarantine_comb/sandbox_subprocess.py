"""Provide SandboxSubprocess: a development-only sandbox that is a temp directory and a process.

DEVELOPMENT ONLY. This sandbox gives a tool a temporary directory, a scrubbed environment and the
running Python interpreter, and nothing more: it has no network namespace, no memory cgroup and no
filesystem isolation, so everything it knows about containing a tool comes from the runner's own
audit hook and resource limits, which are a Python-level control and not a security boundary.
That is exactly what ADR-0047 accepts for `hive tools quarantine <path>` on a developer's machine
and for the unit tests, and exactly why the manifest selects it only by an explicit
`[royal_jelly] sandbox = "subprocess"`, the manifest refuses that setting outside `env = "dev"`,
and the composition root logs a warning when it builds one. Production proof of a tool happens
in `SandboxCell`, inside a Virtual Cell.
It is the one module in the Royal Jelly package that starts a process (the import-linter
contract names it); it does so through asyncio's subprocess API with an argument list.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Built by the composition root (dev manifests, `hive tools quarantine`) and by the tests; used
    by `QuarantineComb`. Calls into `hivemind.cell.session` (CompletedCommand),
    `hivemind.cell.local` (kill_process_tree, for Windows) and the standard
    library.

Key invariants:
    - The child gets only PATH, HOME (the sandbox root), LANG, the two Python hygiene flags and
      what the caller adds: nothing of the parent's environment (tokens, keys) leaks in.
    - The child runs in its own process group, and a timeout kills the whole group.
    - Captured output is capped; the pipes are still drained so the child never blocks on them.
    - `cleanup` removes the whole temporary directory and is idempotent.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for why this exists and its limits.
    - hivemind.royal_jelly.quarantine_comb.sandbox_cell for the production sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from hivemind.cell.local import kill_process_tree
from hivemind.cell.session import CompletedCommand
from hivemind.royal_jelly.quarantine_comb.sandbox import (
    DEFAULT_OUTPUT_BYTES,
    TIMED_OUT_EXIT_CODE,
    CappedOutput,
    check_relative,
)
from waggle.clock import Clock
from waggle.ids import CellId

_TRANSPORT_CLOSE_TURNS = 3  # Loop turns a finished subprocess transport needs to close.
_READ_CHUNK_BYTES = 65536  # Pipe read size: efficient without holding output back long.
_REAP_GRACE_S = 5.0  # After SIGKILL the child is reaped almost at once; this only bounds a wedge.
_DIR_PREFIX = "hive-comb-"  # Names the temp directory so a leftover is recognisable.
_KEPT_ENV = ("SYSTEMROOT", "TZ")  # Variables some platforms need for a child to start at all.

__all__ = ["SandboxSubprocess"]


class SandboxSubprocess:
    """A sandbox in a temporary directory that runs commands as local child processes."""

    def __init__(
        self,
        clock: Clock,
        python: str = sys.executable,
        *,
        output_bytes: int = DEFAULT_OUTPUT_BYTES,
    ) -> None:
        """Create the temporary root and remember how to start Python.

        Args:
            clock: Measures how long a command ran; never `datetime.now()`.
            python: The interpreter the runner starts under; the running one by default, which
                is the interpreter that has the Hive installed.
            output_bytes: Cap on the stdout and stderr one `exec` keeps (`QuarantineLimits`).
        """
        self._clock = clock
        self._python = python
        self._output_bytes = output_bytes
        # mkdtemp creates the directory now (mode 0700), so `root` is real before the first put.
        self._root = Path(tempfile.mkdtemp(prefix=_DIR_PREFIX)).resolve()

    @property
    def root(self) -> Path:
        """The temporary directory every path resolves under."""
        return self._root

    @property
    def cell_id(self) -> CellId | None:
        """Always None: this sandbox is not a Cell."""
        return None

    @property
    def interpreter(self) -> str:
        """The interpreter the runner starts under."""
        return self._python

    async def put(self, relative: Path, data: bytes) -> None:
        """Write `data` under the root, creating parent directories.

        Args:
            relative: A relative path with no `..` segment.
            data: The bytes to write.

        Raises:
            SandboxPathError: `relative` leaves the root.
        """
        target = self._root / check_relative(relative)
        await asyncio.to_thread(_write, target, data)

    async def read(self, relative: Path) -> bytes:
        """Read the file at `relative` under the root.

        Args:
            relative: A relative path with no `..` segment.

        Returns:
            The file's contents.

        Raises:
            SandboxPathError: `relative` leaves the root.
            FileNotFoundError: No such file exists.
        """
        return await asyncio.to_thread((self._root / check_relative(relative)).read_bytes)

    async def exec(
        self, argv: tuple[str, ...], timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CompletedCommand:
        """Run `argv` in the root with a scrubbed environment and a wall-clock kill.

        Args:
            argv: The command and its arguments; never run through a shell.
            timeout_s: Wall seconds before the process group is killed.
            env: Extra environment variables, added over the scrubbed ones.

        Returns:
            The outcome; `TIMED_OUT_EXIT_CODE` when the group had to be killed.
        """
        started = self._clock.monotonic()
        output = CappedOutput(self._output_bytes)
        process = await asyncio.create_subprocess_exec(
            *argv,  # SAFETY: an argument list, never a shell; the Comb builds argv itself.
            cwd=self._root,
            env={**self._scrubbed_env(), **(env or {})},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # Its own group, so a timeout can kill everything it forked.
        )
        timed_out = False
        # Draining both pipes and waiting for exit run together under one wall-clock bound; the
        # bound is the sandbox's, independent of how fast the child writes.
        try:
            await asyncio.wait_for(_drain_and_wait(process, output), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            await _kill_group(process, self._clock)
            # The pumps died with the wait: read what is left so each pipe reaches EOF and its
            # transport closes (one left open warns when the loop closes), bounded in case a
            # grandchild the kill missed still holds the pipe's far end.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_drain_and_wait(process, output), timeout=_REAP_GRACE_S)
        code = TIMED_OUT_EXIT_CODE if timed_out else (process.returncode or 0)
        return output.result(code, self._clock.monotonic() - started)

    async def cleanup(self) -> None:
        """Delete the temporary directory and everything under it."""
        # ignore_errors: a vanished directory is the goal, and a half-removed one must not mask
        # the report the caller is about to return.
        await asyncio.to_thread(shutil.rmtree, self._root, True)

    def _scrubbed_env(self) -> dict[str, str]:
        """Build the child's environment from nothing but what a Python tool needs."""
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self._root),
            "LANG": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",  # No user site-packages: only the sandbox interpreter's own.
            "PYTHONDONTWRITEBYTECODE": "1",  # Nothing writes .pyc files into the source tree.
        }
        for name in _KEPT_ENV:
            if name in os.environ:
                env[name] = os.environ[name]
        return env


def _write(target: Path, data: bytes) -> None:
    """Create `target`'s parent directories, then write `data` to it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


async def _drain_and_wait(process: asyncio.subprocess.Process, output: CappedOutput) -> None:
    """Read both pipes to EOF into `output`, then wait for the child to exit."""
    async with asyncio.TaskGroup() as group:
        group.create_task(_pump(process.stdout, output, is_stderr=False))
        group.create_task(_pump(process.stderr, output, is_stderr=True))
    await process.wait()
    # The transport finishes closing on later loop turns (each pipe's connection-lost and the
    # exit itself are scheduled, not run, when `wait` wakes; Windows' proactor warns of a
    # transport still open when a loop closes), so yield until it is done.
    for _ in range(_TRANSPORT_CLOSE_TURNS):
        await asyncio.sleep(0)


async def _pump(
    stream: asyncio.StreamReader | None, output: CappedOutput, *, is_stderr: bool
) -> None:
    """Move one pipe's bytes into the capped collector until EOF."""
    if stream is None:
        return
    while True:
        # Bounded by the child's pace only; a silent hung child is cut by the caller's wait_for.
        data = await stream.read(_READ_CHUNK_BYTES)
        if not data:
            return
        output.add(is_stderr=is_stderr, data=data)


async def _kill_group(process: asyncio.subprocess.Process, clock: Clock) -> None:
    """Kill the child's whole process group (its tree, on Windows) and reap it."""
    if process.returncode is None:
        try:
            if sys.platform == "win32":
                # No process groups to signal here: the Cell releaser's `taskkill /T` takes the
                # tree, so a grandchild cannot outlive the kill and keep the pipes open (which
                # would also keep their transports from ever closing).
                await kill_process_tree(process.pid, clock)
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # It exited between the check and the kill: nothing left to stop.
    try:
        # SIGKILL cannot be ignored, so this returns at once unless the kernel is wedged.
        await asyncio.wait_for(process.wait(), timeout=_REAP_GRACE_S)
    except TimeoutError:
        return  # Give up reaping; the OS adopts the zombie when this process exits.
