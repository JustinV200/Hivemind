"""Define HiveStandLeaseReleaser: leave the Hive Stand exactly as a lease found it, on release.

`hivemind.cell.lease.RealCellLease.release()` delegates the actual side effects of releasing a
lease to an injected `LeaseReleaser`; `HiveStandLeaseReleaser` is the one the Hive Stand (the
machine the Queen runs on) uses. Three things happen, in order: every process the lease started
that might still be alive is killed, tree and all; every path the lease touched outside scratch is
restored to what it held before (in reverse order, last write first, skipping anything the
operator approved to persist); and the lease's own scratch directory is removed wholesale. This
module also defines `kill_process_tree`, the one piece of process-killing logic both this releaser
and `hivemind.cell.local.session.LocalProcessSession`'s own watchdog and `close()` share, since
"kill the process group led by this pid" is the same operation whether it happens because a
command went over its quota or because the whole lease is being released.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Implements
    `hivemind.cell.lease.LeaseReleaser`; constructed by
    `hivemind.cell.local.source.HiveStandSource.lease` and injected into the `RealCellLease` it
    builds. Calls into the standard library (`os`, `signal`, `asyncio`, `shutil`, on Windows
    `asyncio.create_subprocess_exec("taskkill", ...)`) and `hivemind.cell.lease`/`.errors` only.

Key invariants:
    - `release()` always attempts every step (kill, replay, remove) even when an earlier one
      finds nothing to do; a path that cannot be restored is added to `residual_paths` rather than
      raising, so one bad path never stops the rest of the cleanup.
    - Restore records are replayed in reverse of the order they were recorded (last write first),
      and a record with `persist=True` is skipped: the operator already approved that change to
      stay (codingrules section 8.12/roadmap step 3.17).
    - `kill_process_tree` never raises for a pid that is already dead; it returns `False` instead.
    - Removing scratch retries briefly (`_SCRATCH_REMOVE_ATTEMPTS`) past an `OSError`: a process
      just killed mid-write can hold a file handle open for a beat after the OS reports it dead.

See Also:
    - .claude/roadmap.md step 3.11 for "release() terminates survivors and removes the directory."
    - .claude/roadmap.md step 3.17 for the restore-path replay this releaser performs the lease
      side of.
    - .claude/codingrules.md section 8.7 for "Owned versus leased" and what release() restores.
    - hivemind.cell.lease for LeaseReleaser, RestoreRecord and LeaseReleaseReport.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
import time
from pathlib import Path

from hivemind.cell.lease import LeaseReleaseReport, RealCellLease, RestoreRecord
from waggle.clock import Clock

KILL_GRACE_S = 0.2  # A brief, bounded window for SIGTERM to let a child exit before SIGKILL.
# A process this releaser just killed can hold a large file's write handle open for a brief
# moment after the OS reports it dead (observed on Windows), so a bare rmtree can race a lock
# that is already on its way out. This retry absorbs that without masking a real failure (a
# permissions problem, say) forever: five attempts, 50ms apart, bounds the wait at 0.2s.
_SCRATCH_REMOVE_ATTEMPTS = 5
_SCRATCH_REMOVE_RETRY_S = 0.05

__all__ = ["KILL_GRACE_S", "HiveStandLeaseReleaser", "kill_process_tree"]


class HiveStandLeaseReleaser:
    """Kill survivors, replay restore records and remove scratch: the Hive Stand's own release()."""

    def __init__(self, clock: Clock) -> None:
        """Build a releaser that times its SIGTERM-then-SIGKILL grace period from `clock`.

        Args:
            clock: Source of the grace-period wait between SIGTERM and SIGKILL on POSIX.
        """
        self._clock = clock

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Kill what `lease` started, restore what it touched, and remove its scratch directory.

        Args:
            lease: The lease being released, already transitioned to RELEASING.

        Returns:
            How many processes were killed, which paths (if any) could not be restored or
            removed, and whether the Hive Stand was left exactly as found.
        """
        killed = 0
        for pid in lease.started_pids:
            if await kill_process_tree(pid, self._clock):
                killed += 1
        residual = await asyncio.to_thread(_replay_restore_records, lease.restore_records)
        scratch_removed = await asyncio.to_thread(_remove_scratch, lease.scratch_root)
        if not scratch_removed:
            residual = (*residual, lease.scratch_root)
        return LeaseReleaseReport(
            killed_processes=killed,
            residual_paths=residual,
            is_restored=scratch_removed and not residual,
        )


async def kill_process_tree(pid: int, clock: Clock) -> bool:
    """Kill the process group led by `pid`, tree and all, POSIX and Windows shims side by side.

    Args:
        pid: A process id a `CellSession` started; on POSIX this is also its own process group id
            (every child is spawned with `start_new_session=True`).
        clock: Source of the SIGTERM-then-SIGKILL grace period on POSIX.

    Returns:
        True if the process was alive and a kill was attempted; False if it was already dead.
    """
    if sys.platform == "win32":
        return await _kill_tree_windows(pid)
    else:
        return await _kill_tree_posix(pid, clock)


# ──────────────────────────────────────────────────────────────────────────────
# Killing: POSIX (os.killpg, SIGTERM then SIGKILL) and Windows (taskkill /T /F)
# ──────────────────────────────────────────────────────────────────────────────


async def _kill_tree_posix(pid: int, clock: Clock) -> bool:
    """SIGTERM the process group, wait a short grace period, then SIGKILL any survivor.

    `os.killpg` targets the *group*, not just `pid` itself: every child this package spawns is
    its own session and group leader (`start_new_session=True`), so a descendant that outlives
    its own immediate parent (a daemonized process, say) is still reachable by group id even
    after the original leader pid has already exited -- checking `pid` itself for liveness first
    would miss exactly that case, so this goes straight to `killpg` and reads its own
    `ProcessLookupError` as "nothing left in this group" instead.
    """
    # mypy's platform narrowing only exempts `os.killpg`/`signal.SIGKILL` (typeshed declares
    # both off Windows) inside a matching `if sys.platform == ...:` branch in *this* function --
    # the caller's own check (`kill_process_tree`) does not carry over -- so this repeats it,
    # even though this function is in fact only ever called from that already-guarded call site.
    if sys.platform == "win32":
        return False  # pragma: no cover -- unreachable in practice; see the module docstring.
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return False  # No process in this group -- pid or any descendant -- is alive.
        await clock.sleep(KILL_GRACE_S)
        with contextlib.suppress(ProcessLookupError):
            # The suppressed case: the whole group exited on its own during the grace period.
            os.killpg(pid, signal.SIGKILL)
        return True


async def _kill_tree_windows(pid: int) -> bool:
    """Force-kill the whole process tree rooted at `pid` via `taskkill /T /F /PID`."""
    # SAFETY: an argument list, never shell=True; /T kills the whole tree and /F forces it, the
    # one-step Windows equivalent of the POSIX SIGTERM-then-SIGKILL fallback above. taskkill's
    # own exit code (128/"not found" for an already-dead pid) is read rather than raised on.
    process = await asyncio.create_subprocess_exec(
        "taskkill",
        "/T",
        "/F",
        "/PID",
        str(pid),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    return process.returncode == 0


# ──────────────────────────────────────────────────────────────────────────────
# Restoring paths and removing scratch
# ──────────────────────────────────────────────────────────────────────────────


def _replay_restore_records(records: tuple[RestoreRecord, ...]) -> tuple[Path, ...]:
    """Write every non-persisted record's `prior` bytes back, in reverse order.

    Args:
        records: `RealCellLease.restore_records`, in the order they were recorded.

    Returns:
        The paths that could not be restored, in the order they were attempted.
    """
    residual: list[Path] = []
    for record in reversed(records):
        if record.persist:
            continue  # The operator approved this change to stay (roadmap step 3.17).
        try:
            if record.prior is None:
                record.path.unlink(missing_ok=True)  # It did not exist before the lease touched it.
            else:
                record.path.parent.mkdir(parents=True, exist_ok=True)
                record.path.write_bytes(record.prior)
        except OSError:
            residual.append(record.path)
    return tuple(residual)


def _remove_scratch(scratch_root: Path) -> bool:
    """Remove `scratch_root` wholesale, if it exists, retrying briefly past a lingering lock.

    Args:
        scratch_root: The lease's own scratch directory.

    Returns:
        True once `scratch_root` no longer exists (including if it never did); False if every
        `_SCRATCH_REMOVE_ATTEMPTS` attempt failed.
    """
    if not scratch_root.exists():
        return True
    for attempt in range(_SCRATCH_REMOVE_ATTEMPTS):
        try:
            shutil.rmtree(scratch_root)
        except OSError:
            # A just-killed process's file handle can outlive it by a beat (see this module's
            # own constants above); give the OS a little more time unless this was the last try.
            if attempt + 1 == _SCRATCH_REMOVE_ATTEMPTS:
                return False
            time.sleep(_SCRATCH_REMOVE_RETRY_S)
        else:
            return True
    return False  # Unreachable given _SCRATCH_REMOVE_ATTEMPTS > 0; satisfies mypy's flow analysis.
