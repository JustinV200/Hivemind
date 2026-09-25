"""Spawn one child process and stream its output to an ExitStatus, shared by every CellSession.

`hivemind.cell.local.session.LocalProcessSession` (the Hive Stand) and `hivemind.cell.in_cell.
InCellSession` (roadmap step 5.5: code already running inside its own Virtual Cell) both run
`ExecSpec`s as real child processes and both need the identical concurrent machinery to do it
safely: spawn in its own process group so a watchdog can kill the whole tree, drain stdout and
stderr without holding either back, watch for a timeout (and, when a `ScratchQuota` is given, a
disk-quota breach), and turn a spawn failure into an ordinary failed command instead of a raised
`OSError`. `run_child_process` is that whole sequence, factored out here (roadmap step 5.5: "reuse
the local session's process machinery rather than duplicating it") so the two sessions differ only
in what they pass in -- `LocalProcessSession` gives it a real `ScratchQuota`; `InCellSession` gives
it `quota=None`, since a Virtual Cell's disk is already capped by its `VirtualCellSpec` at the
container level, not by a second, per-command budget this module would otherwise re-enforce.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local`. Called by `hivemind.cell.local.
    session.LocalProcessSession.exec` and by `hivemind.cell.in_cell.InCellSession.exec` (a sibling
    module of this sub-package, not nested inside it, per roadmap step 5.5's own file list).
    `subprocess` is allowed in both call sites (the root `pyproject.toml` import-linter contract
    covers every module under `hivemind.cell.**`). Calls into the standard library
    (`asyncio`, `os`, `subprocess`, `resource` on POSIX), `hivemind.cell.session`/`.errors`,
    `hivemind.cell.local.quota` and `hivemind.cell.local.releaser` (`kill_process_tree`, the same
    kill logic `HiveStandLeaseReleaser` and `InCellLeaseReleaser` both use) only.

Key invariants:
    - `run_child_process` streams `OutputChunk`s in arrival order across both pipes, ending in
      exactly one `ExitStatus`, unless it raises `CommandTimeoutError` or
      `ScratchQuotaExceededError` first -- matching `CellSession.exec`'s own contract exactly, so
      every caller of this function already satisfies that contract for free.
    - `ctx.on_started` is called with the spawned pid before anything else touches the child, so a
      crash between spawn and the first yielded event still leaves the pid where the caller's own
      bookkeeping (`RealCellLease.note_started_process`) can find it.
    - A command that cannot start at all (no such executable, not executable, a missing cwd) never
      lets an `OSError` escape: it yields one stderr chunk and `EXIT_COMMAND_NOT_STARTED` instead,
      so the bee that proposed it reads an ordinary failed command, not a crash.
    - A run cancelled before its child exits kills the child's whole tree before the cancel
      completes (roadmap step 10.6c), so a stopped, respawned or quarantined bee leaves nothing it
      started running; the lease's own release still kills every pid it recorded, as before.
    - `ctx.quota is None` skips every quota check (`_over_quota` always False, no `RLIMIT_FSIZE`
      set on POSIX): `InCellSession`'s own choice, documented on its own module.

See Also:
    - .claude/roadmap.md step 5.5 for "reuse the local session's process machinery... if sharing
      forces a refactor of cell/local/ keep it minimal and behaviour-preserving."
    - hivemind.cell.local.session for LocalProcessSession, this module's first caller.
    - hivemind.cell.in_cell for InCellSession, this module's second caller.
    - hivemind.cell.local.quota for ScratchQuota, QUOTA_SAMPLE_INTERVAL_S and the watchdog cadence.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from hivemind.cell.errors import CommandTimeoutError, ScratchQuotaExceededError
from hivemind.cell.local.quota import QUOTA_SAMPLE_INTERVAL_S, ScratchQuota
from hivemind.cell.local.releaser import kill_process_tree
from hivemind.cell.session import ExecEvent, ExecSpec, ExitStatus, OutputChunk, OutputStream
from hivemind.common.errors import InvariantViolationError
from waggle.clock import Clock

if sys.platform != "win32":
    import resource  # POSIX only; typeshed (and mypy's platform narrowing) agree.

_READ_CHUNK_BYTES = 65536  # 64 KiB per pipe read: efficient without holding output back long.

# The exit code a POSIX shell gives a command it cannot find. A command a caller cannot even start
# (no such executable on this OS, not executable, a missing cwd) is reported with it and the
# reason on stderr, so the bee that proposed it reads an ordinary failed command.
EXIT_COMMAND_NOT_STARTED = 127

__all__ = ["EXIT_COMMAND_NOT_STARTED", "ProcessContext", "resolve_cwd", "run_child_process"]


class _Violation(Enum):
    """Why the watchdog killed a running command, if it did."""

    TIMEOUT = auto()
    QUOTA = auto()


@dataclass(frozen=True, slots=True)
class ProcessContext:
    """What `run_child_process` needs beyond the `ExecSpec` itself (codingrules 5.1).

    Attributes:
        clock: Source of every timestamp and duration; never `datetime.now()`.
        scratch_dir: This command's working directory when `ExecSpec.cwd` is relative or absent.
        quota: The byte cap the watchdog enforces on `scratch_dir`, or None to skip quota
            enforcement entirely (`InCellSession`'s own choice; see this module's docstring).
        on_started: Called once, with the spawned child's pid, the moment it is known -- before
            anything else touches the child -- so the caller's own lease bookkeeping
            (`RealCellLease.note_started_process`) never misses a pid to a crash mid-exec.
    """

    clock: Clock
    scratch_dir: Path
    quota: ScratchQuota | None
    on_started: Callable[[int], None]


def resolve_cwd(scratch_dir: Path, cwd: Path | None) -> Path:
    """Resolve `ExecSpec.cwd` against `scratch_dir`, matching `resolve_scratch_path`'s own rule.

    Args:
        scratch_dir: The session's own scratch directory.
        cwd: `ExecSpec.cwd`; None means `scratch_dir` itself.

    Returns:
        `scratch_dir` when `cwd` is None, `cwd` unchanged when already absolute, else `cwd`
        joined under `scratch_dir`.
    """
    if cwd is None:
        return scratch_dir
    return cwd if cwd.is_absolute() else scratch_dir / cwd


async def run_child_process(spec: ExecSpec, ctx: ProcessContext) -> AsyncIterator[ExecEvent]:
    """Spawn `spec` as a real child process and stream events to completion.

    Args:
        spec: The command to run.
        ctx: Everything else this run needs: clock, scratch directory, optional quota and the
            started-pid callback.

    Yields:
        `OutputChunk`s in arrival order across both stdout and stderr, then one `ExitStatus`. A
        command that cannot start at all yields one stderr chunk naming the reason, then an
        `ExitStatus` of `EXIT_COMMAND_NOT_STARTED`; an `OSError` from the spawn never escapes.

    Raises:
        CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
        ScratchQuotaExceededError: `ctx.quota` is set and `ctx.scratch_dir` grew past it.
    """
    cwd = resolve_cwd(ctx.scratch_dir, spec.cwd)
    env = {**os.environ, **spec.env}
    start = ctx.clock.monotonic()  # never datetime.now(): only the elapsed span matters.
    quota_bytes = ctx.quota.quota_bytes if ctx.quota is not None else None
    try:
        process = await _spawn(spec, cwd, env, quota_bytes)
    except OSError as error:
        # Reported the way a shell reports a command it cannot find, not raised: to the bee that
        # proposed it this is one more failed command to read and correct, whereas an exception
        # escaping its tool call is a crash of the bee itself, an Alarm, and an unneeded escalation.
        reason = f"{spec.argv[0]}: {error.strerror or error}".encode()
        yield OutputChunk(stream=OutputStream.STDERR, data=reason)
        duration_s = ctx.clock.monotonic() - start
        yield ExitStatus(code=EXIT_COMMAND_NOT_STARTED, duration_s=duration_s)
        return
    # Recorded before anything else touches the child, so a crash mid-exec still leaves the pid
    # where the caller's own close()/release() can find and kill it (codingrules section 8.7).
    ctx.on_started(process.pid)
    try:
        await _write_stdin(process, spec.stdin)
        async for event in _run_to_completion(process, spec, ctx, start):
            yield event
    except asyncio.CancelledError:
        # Cancelled before its child exited: the bee running it was stopped, respawned or
        # quarantined (roadmap 10.6c: "cancel, kill the tracked process"). The child's tree dies
        # with the run, not later at lease release, so nothing a cancelled bee started outlives it.
        if process.returncode is None:
            await kill_process_tree(process.pid, ctx.clock)
        raise


# ──────────────────────────────────────────────────────────────────────────────
# Running one command: spawn, drain both pipes, watch, report the outcome
# ──────────────────────────────────────────────────────────────────────────────


async def _run_to_completion(
    process: asyncio.subprocess.Process, spec: ExecSpec, ctx: ProcessContext, start: float
) -> AsyncIterator[ExecEvent]:
    """Drain an already-spawned process's output and watchdog, yielding events as they arrive."""
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
        if ctx.quota is None:
            # _over_quota (the only source of a QUOTA violation) is always False with no quota
            # configured, so _watch can never actually reach this; guards mypy's narrowing instead
            # of an assert (codingrules 15: no bare assert as a control-flow guard outside tests).
            raise InvariantViolationError("_watch reported QUOTA with no ScratchQuota configured.")
        observed = ctx.quota.sizer(ctx.scratch_dir)
        raise ScratchQuotaExceededError(ctx.scratch_dir, ctx.quota.quota_bytes, observed)
    yield ExitStatus(code=await process.wait(), duration_s=ctx.clock.monotonic() - start)


async def _spawn(
    spec: ExecSpec, cwd: Path, env: dict[str, str], quota_bytes: int | None
) -> asyncio.subprocess.Process:
    """Start `spec` as a child in its own process group, POSIX and Windows side by side.

    Args:
        spec: The command to run.
        cwd: This command's already-resolved working directory.
        env: This command's already-merged environment.
        quota_bytes: The scratch quota, applied as a POSIX child's own RLIMIT_FSIZE; None skips
            it (no quota configured for this run).

    Returns:
        The started process, with its stdout/stderr piped and its stdin piped when `spec` carries
        any.
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
            # exec() replaces it. Skipped entirely when no quota was configured for this run.
            if quota_bytes is not None:
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
    process: asyncio.subprocess.Process, timeout_s: float, start: float, ctx: ProcessContext
) -> _Violation | None:
    """Sample elapsed time and (when `ctx.quota` is set) scratch usage while the child runs."""
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


def _over_quota(ctx: ProcessContext) -> bool:
    """Return whether `ctx.scratch_dir` exceeds `ctx.quota`; always False with no quota set."""
    if ctx.quota is None:
        return False
    return ctx.quota.sizer(ctx.scratch_dir) > ctx.quota.quota_bytes


async def _pump_stream(
    stream: asyncio.StreamReader | None,
    kind: OutputStream,
    queue: asyncio.Queue[OutputChunk | None],
) -> None:
    """Read `stream` until EOF, pushing each non-empty read as an OutputChunk, then a sentinel."""
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
