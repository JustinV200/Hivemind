"""Define InCellSession: a CellSession for code already running inside its own Virtual Cell.

Every other `CellSession` reaches its Cell from outside: `LocalProcessSession` runs on the Hive
Stand (the machine the Queen runs on) on behalf of a lease someone else could contend for, and a
future `PollenSession` reaches a Swarm device over Waggle. `InCellSession` is the odd one out
(roadmap step 5.5): the Warden and every sub-bee it spawns already run *inside* the Virtual Cell
(a container or VM the Hive provisioned and will destroy) they are working on, so `exec` runs
commands as ordinary local subprocesses of this same process, and `put_file`/`get_file`/
`delete_file` touch a directory inside the Cell directly -- there is no network hop and no other
tenant to isolate from. `InCellLeaseReleaser` is this session's own `LeaseReleaser`
(`hivemind.cell.lease.RealCellLease.release()`'s injected side effect): unlike the Hive Stand's
`HiveStandLeaseReleaser`, it never restores a path or removes scratch, because there is nothing to
leave "as found" -- the whole Cell is disposable and the Undertaker destroys it once this Warden
reports done, so *destroy* is the cleanup, not `release()`.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside the `cell` package -- a single-module sibling of
    `hivemind.cell.local`, not nested inside it, exactly the path roadmap step 5.5 names
    (`cell/in_cell.py`). `subprocess` is allowed here: the root `pyproject.toml` import-linter
    contract "subprocess only from Cell sessions and hive backends" already covers every module
    under `hivemind.cell.**`, not only `hivemind.cell.local`. Reuses `hivemind.cell.local.process`'s
    shared child-process machinery rather than duplicating it (the roadmap step's own instruction)
    and `hivemind.cell.local.releaser.kill_process_tree` for `close()`'s own cleanup, the same
    helper `HiveStandLeaseReleaser` uses. Constructed by
    `hivemind.wardens.spawn.in_cell.InCellSpawnSource.open_session`.

Key invariants:
    - THE key invariant (roadmap step 5.5): a Virtual Cell is disposable. `InCellSession` and
      `InCellLeaseReleaser` never restore a path and never remove scratch on close or release --
      there is no borrowed host to leave as found, because the whole Cell is destroyed by the
      Undertaker once its Warden reports done (codingrules section 8.7's "Owned versus leased").
      `InCellLeaseReleaser.release()` only kills survivor processes, the one piece of hygiene worth
      doing before a container exits on its own.
    - No scratch quota watchdog: a Virtual Cell's disk is already capped at the container level by
      its `VirtualCellSpec` (a `hive` concern, out of this module's reach), not by a second,
      per-command budget this session would otherwise have to re-enforce.
    - Every subprocess this session spawns is POSIX-only (`start_new_session`, `RLIMIT_FSIZE`
      skipped since no quota is configured): every Virtual Cell image is Ubuntu (codingrules
      section 2), so `InCellSession` carries none of `LocalProcessSession`'s Windows shim.
    - `close()` is idempotent and kills every pid this session's lease recorded as started.

See Also:
    - .claude/roadmap.md step 5.5 for "cell/in_cell.py: InCellSession... no borrowed-host
      lease/restore because the whole Cell is disposable."
    - .claude/codingrules.md section 8.7 for "Owned versus leased", the distinction this module's
      own key invariant restates for a Virtual Cell.
    - hivemind.cell.local.session for LocalProcessSession, the sibling this one shares its process
      machinery with.
    - hivemind.cell.local.process for run_child_process/ProcessContext, the shared machinery.
    - hivemind.wardens.spawn.in_cell for InCellSpawnSource, the RealCellSource that hands this
      session out to a Warden already running inside its own Cell.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from hivemind.cell.errors import SessionClosedError
from hivemind.cell.lease import LeaseReleaseReport, RealCellLease
from hivemind.cell.local.process import ProcessContext, run_child_process
from hivemind.cell.local.releaser import kill_process_tree
from hivemind.cell.session import ExecEvent, ExecSpec, resolve_scratch_path
from waggle.clock import Clock

__all__ = ["InCellLeaseReleaser", "InCellSession"]


class InCellSession:
    """A CellSession over a real OS process, for a Warden already running inside its own Cell."""

    def __init__(self, lease: RealCellLease, clock: Clock) -> None:
        """Build a session scoped to `lease`'s scratch directory and allowed paths.

        Args:
            lease: The OPEN lease this session runs commands on behalf of, from
                `hivemind.wardens.spawn.in_cell.InCellSpawnSource`.
            clock: Source of every timestamp and duration; never `datetime.now()`.
        """
        self._lease = lease
        self._scratch_dir = lease.scratch_root
        self._allowed_paths = lease.allowed_paths
        self._clock = clock
        self._open = True

    @property
    def scratch_dir(self) -> Path:
        """This session's scratch directory, inside the Virtual Cell."""
        return self._scratch_dir

    @property
    def is_open(self) -> bool:
        """Whether this session still accepts calls."""
        return self._open

    async def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Run `spec` as a local subprocess of this same Cell, ending in one ExitStatus.

        Args:
            spec: The command to run.

        Yields:
            OutputChunk as output arrives across both stdout and stderr, then one ExitStatus. A
            command that cannot start at all yields one stderr chunk naming the reason, then an
            ExitStatus of `EXIT_COMMAND_NOT_STARTED`; an OSError from the spawn never escapes.

        Raises:
            SessionClosedError: This session is closed.
            CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
        """
        if not self._open:
            raise SessionClosedError(self._scratch_dir)
        ctx = ProcessContext(
            clock=self._clock,
            scratch_dir=self._scratch_dir,
            # Key invariant: no scratch-usage watchdog inside a Virtual Cell (module docstring).
            quota=None,
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
            # codingrules section 12: a write outside scratch is always audited on the lease, even
            # though (module docstring) nothing here will ever be individually restored.
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


class InCellLeaseReleaser:
    """Kill survivor processes and report success: no restore, no scratch removal.

    The Virtual Cell this lease is scoped to is disposable (module docstring's key invariant):
    once this Warden reports done, the Undertaker destroys the whole Cell, so there is no borrowed
    host to leave as found and nothing this releaser could restore would ever outlive the Cell
    anyway. Killing survivors is still worth doing -- a well-behaved process stops what it started
    before its container exits, the same hygiene `close()` performs -- but replaying restore
    records or removing the scratch directory would be work with no observer left to see it.
    """

    def __init__(self, clock: Clock) -> None:
        """Build a releaser that times its kill grace period from `clock`.

        Args:
            clock: Source of the grace-period wait `kill_process_tree` uses between SIGTERM and
                SIGKILL on POSIX.
        """
        self._clock = clock

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Kill every process `lease` started; never restore a path or remove scratch.

        Args:
            lease: The lease being released, already transitioned to RELEASING.

        Returns:
            A report with `is_restored=True` and no residual paths: there is nothing to restore
            (this module's own key invariant), so the disposable Cell always counts as "left as
            found" the moment its survivors are gone.
        """
        killed = 0
        for pid in lease.started_pids:
            if await kill_process_tree(pid, self._clock):
                killed += 1
        return LeaseReleaseReport(killed_processes=killed, residual_paths=(), is_restored=True)


def _is_outside_scratch(scratch_dir: Path, resolved: Path) -> bool:
    """Return whether an already-resolved path lands outside `scratch_dir`."""
    scratch = scratch_dir.resolve(strict=False)
    return not (resolved == scratch or scratch in resolved.parents)


def _write_file(path: Path, data: bytes) -> None:
    """Create `path`'s parent directories if needed, then write `data` to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
