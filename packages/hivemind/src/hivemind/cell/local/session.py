"""Define LocalProcessSession: a CellSession over a real OS process, standard library only.

`LocalProcessSession` is the Hive Stand's (the machine the Queen runs on) implementation of
`hivemind.cell.session.CellSession`: `exec` runs a command as a real child process through
`asyncio.create_subprocess_exec` (argument lists only, never a shell), streaming both its stdout
and stderr back as they arrive and ending in exactly one `ExitStatus`; `put_file`/`get_file`/
`delete_file` read and write real files under the lease's own scratch directory. The class itself
stays a thin adapter (codingrules section 8.3): spawning, draining and watching one command are
each module-level functions below it, called with the session's state passed explicitly rather
than carried as more instance methods. Every child is started in its own process group
(`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows) so a watchdog can kill
the whole tree, never just the one process a caller happened to name, on two conditions: the
command's own `timeout_s` elapses, or its lease's scratch directory grows past the configured
`ScratchQuota` (`hivemind.cell.local.quota`) -- the watchdog samples the directory's size, through
`ScratchQuota.sizer` (real by default, injectable for a test), every `QUOTA_SAMPLE_INTERVAL_S`
while the child runs and once more right after it exits, so a breach in a command's last moments
is still caught. On POSIX a second, cheaper defence runs alongside the watchdog: `RLIMIT_FSIZE`
set on the child via `preexec_fn` stops a single file from blowing straight through the quota
between watchdog samples.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Implements
    `hivemind.cell.session.CellSession`; constructed by
    `hivemind.cell.local.source.HiveStandSource.open_session`. Calls into the standard library
    (`asyncio`, `os`, `subprocess`, `resource` on POSIX) and `hivemind.cell.lease`,
    `hivemind.cell.local.quota` and `hivemind.cell.local.releaser` only.

Key invariants:
    - `exec` streams `OutputChunk`s in arrival order across both pipes, ending in exactly one
      `ExitStatus`, unless it raises `CommandTimeoutError` or `ScratchQuotaExceededError` first,
      matching the `CellSession.exec` contract exactly.
    - Every started pid is reported to `self._lease` (`note_started_process`) before this session
      does anything else with the child, so a crash between spawn and the first yield still leaves
      the pid recorded for `close()` or a later `release()` to find and kill.
    - `close()` is idempotent and kills every pid this session's lease recorded as started,
      whether or not this particular session instance is the one that started it.

See Also:
    - .claude/roadmap.md step 3.11 for the watchdog, RLIMIT_FSIZE and process-group requirements.
    - docs/adr/0010-cells-are-real-or-virtual-terminal-first.md for "LocalProcessSession
      implements CellSession over asyncio.create_subprocess_exec."
    - hivemind.cell.session for the CellSession protocol this class implements.
    - hivemind.cell.local.quota for ScratchQuota, DirectorySizer, QUOTA_SAMPLE_INTERVAL_S and
      directory_size_bytes, the watchdog's own measuring stick.
    - hivemind.cell.local.releaser for kill_process_tree, the shared kill logic this session's
      watchdog and close() both use.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from hivemind.cell.errors import CommandTimeoutError, ScratchQuotaExceededError, SessionClosedError
from hivemind.cell.lease import RealCellLease
from hivemind.cell.local.quota import QUOTA_SAMPLE_INTERVAL_S, ScratchQuota
from hivemind.cell.local.releaser import kill_process_tree
from hivemind.cell.session import (
    ExecEvent,
    ExecSpec,
    ExitStatus,
    OutputChunk,
    OutputStream,
    resolve_scratch_path,
)
from waggle.clock import Clock

if sys.platform != "win32":
    import resource  # POSIX only; typeshed (and mypy's platform narrowing) agree.

_READ_CHUNK_BYTES = 65536  # 64 KiB per pipe read: efficient without holding output back long.

__all__ = ["LocalProcessSession"]


class _Violation(Enum):
    """Why the watchdog killed a running command, if it did."""

    TIMEOUT = auto()
    QUOTA = auto()


@dataclass(frozen=True, slots=True)
class _RunContext:
    """The state `_run_to_completion` and `_watch` need beyond the process itself.

    Bundled so both functions stay within codingrules 5.1's five-parameter limit; not part of
    this module's public surface (LocalProcessSession's own `__init__` still takes the three
    fields separately, matching the `RealCellLease`/`ScratchQuota`/`Clock` seam other code binds).
    """

    quota: ScratchQuota
    clock: Clock
    scratch_dir: Path


class LocalProcessSession:
    """A CellSession over a real OS process on the Hive Stand: asyncio subprocess, stdlib only."""

    def __init__(self, lease: RealCellLease, quota: ScratchQuota, clock: Clock) -> None:
        """Build a session scoped to `lease`'s scratch directory and allowed paths.

        Args:
            lease: The OPEN lease this session runs commands on behalf of.
            quota: The byte cap this session's watchdog enforces on `lease.scratch_root`.
            clock: Source of every timestamp, duration and grace-period wait; never
                `datetime.now()` or a bare `asyncio.sleep`.
        """
        self._lease = lease
        self._scratch_dir = lease.scratch_root
        self._allowed_paths = lease.allowed_paths
        self._quota = quota
        self._clock = clock
        self._open = True

    @property
    def scratch_dir(self) -> Path:
        """This session's scratch directory."""
        return self._scratch_dir

    @property
    def is_open(self) -> bool:
        """Whether this session still accepts calls."""
        return self._open

    async def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Run `spec` as a real child process, streaming output and ending in one ExitStatus.

        Args:
            spec: The command to run.

        Yields:
            OutputChunk as output arrives across both stdout and stderr, then one ExitStatus.

        Raises:
            SessionClosedError: This session is closed.
            CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
            ScratchQuotaExceededError: `lease.scratch_root` grew past its configured quota.
        """
        if not self._open:
            raise SessionClosedError(self._scratch_dir)
        cwd = _resolve_cwd(self._scratch_dir, spec.cwd)
        env = {**os.environ, **spec.env}
        start = self._clock.monotonic()  # never datetime.now(): only the elapsed span matters.
        process = await _spawn(spec, cwd, env, self._quota.quota_bytes)
        # Recorded before anything else touches the child, so a crash mid-exec still leaves the
        # pid where close()/release() can find and kill it (codingrules section 8.7).
        self._lease.note_started_process(process.pid)
        await _write_stdin(process, spec.stdin)
        ctx = _RunContext(quota=self._quota, clock=self._clock, scratch_dir=self._scratch_dir)
        async for event in _run_to_completion(process, spec, ctx, start):
            yield event

    async def put_file(self, path: Path, data: bytes) -> None:
        """Write `data` to `path` under scratch (or an allowed path outside it).

        Args:
            path: Where to write; relative to `scratch_dir` when relative.
            data: The bytes to write.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and every allowed path.
        """
        if not self._open:
            raise SessionClosedError(self._scratch_dir)
        resolved = resolve_scratch_path(self._scratch_dir, path, self._allowed_paths)
        if _is_outside_scratch(self._scratch_dir, resolved):
            # codingrules section 12: a write outside scratch is always audited on the lease.
            await self._lease.note_touched_path(resolved)
        await asyncio.to_thread(_write_file, resolved, data)

    async def get_file(self, path: Path) -> bytes:
        """Read and return the bytes at `path`.

        Args:
            path: Where to read from; relative to `scratch_dir` when relative.

        Returns:
            The file's full contents.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and every allowed path.
            FileNotFoundError: No such file exists.
        """
        if not self._open:
            raise SessionClosedError(self._scratch_dir)
        resolved = resolve_scratch_path(self._scratch_dir, path, self._allowed_paths)
        if not resolved.exists():
            raise FileNotFoundError(f"No file at {resolved}.")
        return await asyncio.to_thread(resolved.read_bytes)

    async def delete_file(self, path: Path) -> None:
        """Remove the file at `path`.

        Args:
            path: What to remove; relative to `scratch_dir` when relative.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and every allowed path.
            FileNotFoundError: No such file exists.
        """
        if not self._open:
            raise SessionClosedError(self._scratch_dir)
        resolved = resolve_scratch_path(self._scratch_dir, path, self._allowed_paths)
        if _is_outside_scratch(self._scratch_dir, resolved):
            await self._lease.note_touched_path(resolved)
        if not resolved.exists():
            raise FileNotFoundError(f"No file at {resolved}.")
        await asyncio.to_thread(resolved.unlink)

    async def close(self) -> None:
        """Close this session, killing every pid its lease recorded as started. Idempotent."""
        if not self._open:
            return
        self._open = False
        for pid in self._lease.started_pids:
            await kill_process_tree(pid, self._clock)


# ──────────────────────────────────────────────────────────────────────────────
# Path helpers (pure; no I/O)
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_cwd(scratch_dir: Path, cwd: Path | None) -> Path:
    """Resolve ExecSpec.cwd against scratch_dir, matching resolve_scratch_path's own rule."""
    if cwd is None:
        return scratch_dir
    return cwd if cwd.is_absolute() else scratch_dir / cwd


def _is_outside_scratch(scratch_dir: Path, resolved: Path) -> bool:
    """Return whether an already-resolved path lands outside `scratch_dir`."""
    scratch = scratch_dir.resolve(strict=False)
    return not (resolved == scratch or scratch in resolved.parents)


# ──────────────────────────────────────────────────────────────────────────────
# Running one command: spawn, drain both pipes, watch, report the outcome
# ──────────────────────────────────────────────────────────────────────────────


async def _run_to_completion(
    process: asyncio.subprocess.Process, spec: ExecSpec, ctx: _RunContext, start: float
) -> AsyncIterator[ExecEvent]:
    """Drain an already-spawned process's output and watchdog, yielding events as they arrive.

    Args:
        process: The already-started, already-registered child.
        spec: The command that was run; only its `argv`/`timeout_s` are read here.
        ctx: The quota, clock and scratch directory the watchdog reads.
        start: `ctx.clock.monotonic()` at the moment `process` was spawned.

    Yields:
        OutputChunk as output arrives across both stdout and stderr, then one ExitStatus.

    Raises:
        CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
        ScratchQuotaExceededError: `ctx.scratch_dir` grew past `ctx.quota`.
    """
    # Three concurrent tasks (codingrules section 11: structured concurrency via TaskGroup, never
    # a dropped create_task): one reader per pipe, and the watchdog. The two readers push chunks
    # (and a None sentinel each) onto one queue so this generator can yield them in arrival order
    # across both streams while the watchdog runs alongside, independent of consumer speed.
    queue: asyncio.Queue[OutputChunk | None] = asyncio.Queue()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(_pump_stream(process.stdout, OutputStream.STDOUT, queue))
        tg.create_task(_pump_stream(process.stderr, OutputStream.STDERR, queue))
        watchdog = tg.create_task(_watch(process, spec.timeout_s, start, ctx))
        pending_streams = 2
        while pending_streams > 0:
            item = await queue.get()
            if item is None:
                pending_streams -= 1
                continue
            yield item
        violation = await watchdog
    if violation is _Violation.TIMEOUT:
        raise CommandTimeoutError(spec.argv, spec.timeout_s)
    if violation is _Violation.QUOTA:
        # Re-reads through the same injected sizer _watch used to decide the breach (never the
        # bare directory_size_bytes function), so a test's scripted sizer -- and this error's own
        # observed_bytes -- agree on exactly one number instead of two independent filesystem
        # reads racing each other.
        observed = ctx.quota.sizer(ctx.scratch_dir)
        raise ScratchQuotaExceededError(ctx.scratch_dir, ctx.quota.quota_bytes, observed)
    yield ExitStatus(code=await process.wait(), duration_s=ctx.clock.monotonic() - start)


async def _spawn(
    spec: ExecSpec, cwd: Path, env: dict[str, str], quota_bytes: int
) -> asyncio.subprocess.Process:
    """Start `spec` as a child in its own process group, POSIX and Windows side by side.

    Args:
        spec: The command to run.
        cwd: This command's already-resolved working directory.
        env: This command's already-merged environment.
        quota_bytes: The scratch quota, applied as a POSIX child's own RLIMIT_FSIZE.

    Returns:
        The started process, with its stdout/stderr piped and its stdin piped when `spec`
        carries any.
    """
    stdin = asyncio.subprocess.PIPE if spec.stdin is not None else None
    if sys.platform == "win32":
        return await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:

        def _limit_fsize() -> None:
            # preexec_fn is unsafe with threads in general (the stdlib's own warning), but the
            # roadmap step calls for RLIMIT_FSIZE as a second, cheaper line of defence alongside
            # the watchdog, catching a single file that blows through the quota between samples;
            # asyncio's subprocess spawn path runs this exactly once, in the forked child, before
            # exec() replaces it.
            resource.setrlimit(resource.RLIMIT_FSIZE, (quota_bytes, quota_bytes))

        return await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_limit_fsize,
        )


async def _watch(
    process: asyncio.subprocess.Process, timeout_s: float, start: float, ctx: _RunContext
) -> _Violation | None:
    """Sample elapsed time and scratch usage every QUOTA_SAMPLE_INTERVAL_S while it runs.

    Args:
        process: The running child to watch.
        timeout_s: The command's own deadline, from `ExecSpec.timeout_s`.
        start: `ctx.clock.monotonic()` at the moment this command started.
        ctx: The quota, clock and scratch directory to sample.

    Returns:
        Why the watchdog killed `process`, or None if it exited on its own within its quota.
    """
    while True:
        try:
            # An external await with a bound: QUOTA_SAMPLE_INTERVAL_S, the watchdog's own tick.
            # TimeoutError here means "still running", not a failure.
            async with asyncio.timeout(QUOTA_SAMPLE_INTERVAL_S):
                await process.wait()
        except TimeoutError:
            # A tick with nothing to report: still running, still under quota. Check timeout
            # first -- a command that is both over time and over quota is reported as a timeout,
            # matching CellSession.exec's contract of raising exactly one of the two.
            if ctx.clock.monotonic() - start >= timeout_s:
                await kill_process_tree(process.pid, ctx.clock)
                return _Violation.TIMEOUT
            if _over_quota(ctx):
                await kill_process_tree(process.pid, ctx.clock)
                return _Violation.QUOTA
            continue
        # The process exited on its own; one more sample per the roadmap step's "and once more
        # after it exits" rule, so a breach in the command's last moments is still caught.
        return _Violation.QUOTA if _over_quota(ctx) else None


def _over_quota(ctx: _RunContext) -> bool:
    """Return whether `ctx.scratch_dir` currently exceeds `ctx.quota`, per its own sizer.

    Reads `ctx.quota.sizer` rather than calling `directory_size_bytes` directly, so a test can
    inject a controllable `DirectorySizer` (`hivemind.cell.local.quota`) instead of depending on
    a real subprocess's write speed racing the watchdog's own poll interval.
    """
    return ctx.quota.sizer(ctx.scratch_dir) > ctx.quota.quota_bytes


async def _pump_stream(
    stream: asyncio.StreamReader | None,
    kind: OutputStream,
    queue: asyncio.Queue[OutputChunk | None],
) -> None:
    """Read `stream` until EOF, pushing each non-empty read as an OutputChunk, then a sentinel.

    Args:
        stream: The child's stdout or stderr; None only if the process was spawned without one.
        kind: Which stream this is, stamped on every chunk pushed.
        queue: Where chunks (and the final `None` sentinel) are pushed, for exec() to drain.
    """
    if stream is not None:
        while True:
            # An external await: bounded only by the child's own output pace; a hung child is
            # caught by the watchdog's timeout check running concurrently, not by this read.
            data = await stream.read(_READ_CHUNK_BYTES)
            if not data:
                break
            await queue.put(OutputChunk(stream=kind, data=data))
    await queue.put(None)


async def _write_stdin(process: asyncio.subprocess.Process, data: bytes | None) -> None:
    """Write `data` to the child's stdin and close it, if there is any to write."""
    if data is None or process.stdin is None:
        return
    process.stdin.write(data)
    await process.stdin.drain()  # An external await: bounded by the OS pipe buffer draining.
    process.stdin.close()


def _write_file(path: Path, data: bytes) -> None:
    """Create `path`'s parent directories if needed, then write `data` to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
