"""Define LocalProcessSession: a CellSession over a real OS process, standard library only.

`LocalProcessSession` is the Hive Stand's (the machine the Queen runs on) implementation of
`hivemind.cell.session.CellSession`: `exec` runs a command as a real child process, streaming both
its stdout and stderr back as they arrive and ending in exactly one `ExitStatus`; `put_file`/
`get_file`/`delete_file` read and write real files under the lease's own scratch directory. The
class itself stays a thin adapter (codingrules section 8.3): the concurrent spawn/drain/watchdog
machinery `exec` drives lives in `hivemind.cell.local.process` (roadmap step 5.5's own refactor),
shared with `hivemind.cell.in_cell.InCellSession` rather than duplicated, so this class's own job
is narrowed to resolving paths, wiring this session's `RealCellLease` into that shared machinery's
`on_started`/`note_touched_path` callbacks, and closing.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Implements
    `hivemind.cell.session.CellSession`; constructed by
    `hivemind.cell.local.source.HiveStandSource.open_session`. Calls into `hivemind.cell.lease`,
    `hivemind.cell.local.process` (the shared child-process machinery), `hivemind.cell.local.quota`
    and `hivemind.cell.local.releaser` only.

Key invariants:
    - `exec` streams `OutputChunk`s in arrival order across both pipes, ending in exactly one
      `ExitStatus`, unless it raises `CommandTimeoutError` or `ScratchQuotaExceededError` first
      (`hivemind.cell.local.process.run_child_process`'s own contract, matching `CellSession.exec`
      exactly).
    - Every started pid is reported to `self._lease` (`note_started_process`) before this session
      does anything else with the child, so a crash between spawn and the first yield still leaves
      the pid recorded for `close()` or a later `release()` to find and kill.
    - `close()` is idempotent and kills every pid this session's lease recorded as started,
      whether or not this particular session instance is the one that started it.

See Also:
    - .claude/roadmap.md step 3.11 for the watchdog, RLIMIT_FSIZE and process-group requirements.
    - .claude/roadmap.md step 5.5 for the process.py extraction this module was narrowed by.
    - docs/adr/0010-cells-are-real-or-virtual-terminal-first.md for "LocalProcessSession
      implements CellSession over asyncio.create_subprocess_exec."
    - hivemind.cell.session for the CellSession protocol this class implements.
    - hivemind.cell.local.process for run_child_process/ProcessContext, the shared machinery.
    - hivemind.cell.local.quota for ScratchQuota, this session's own watchdog cap.
    - hivemind.cell.local.releaser for kill_process_tree, the shared kill logic this session's
      close() uses.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from hivemind.cell.errors import SessionClosedError
from hivemind.cell.lease import RealCellLease
from hivemind.cell.local.process import ProcessContext, run_child_process
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.releaser import kill_process_tree
from hivemind.cell.session import ExecEvent, ExecSpec, resolve_scratch_path
from waggle.clock import Clock

__all__ = ["LocalProcessSession"]


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
            OutputChunk as output arrives across both stdout and stderr, then one ExitStatus. A
            command that cannot start at all yields one stderr chunk naming the reason, then an
            ExitStatus of `EXIT_COMMAND_NOT_STARTED`; an OSError from the spawn never escapes.

        Raises:
            SessionClosedError: This session is closed.
            CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
            ScratchQuotaExceededError: `lease.scratch_root` grew past its configured quota.
        """
        if not self._open:
            raise SessionClosedError(self._scratch_dir)
        ctx = ProcessContext(
            clock=self._clock,
            scratch_dir=self._scratch_dir,
            quota=self._quota,
            on_started=self._lease.note_started_process,
        )
        async for event in run_child_process(spec, ctx):
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


def _is_outside_scratch(scratch_dir: Path, resolved: Path) -> bool:
    """Return whether an already-resolved path lands outside `scratch_dir`."""
    scratch = scratch_dir.resolve(strict=False)
    return not (resolved == scratch or scratch in resolved.parents)


def _write_file(path: Path, data: bytes) -> None:
    """Create `path`'s parent directories if needed, then write `data` to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
