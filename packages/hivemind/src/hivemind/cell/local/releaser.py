"""Define HiveStandLeaseReleaser: leave the Hive Stand exactly as a lease found it, on release.

`hivemind.cell.lease.RealCellLease.release()` delegates the actual side effects of releasing a
lease to an injected `LeaseReleaser`; `HiveStandLeaseReleaser` is the one the Hive Stand (the
machine the Queen runs on) uses. Four things happen, in order: every process the lease started
that might still be alive is killed, tree and all; every path the lease touched outside scratch is
either restored to what it held before, or, for a path with at least one `persist=True` record
(roadmap step 5.0a), read back and written to the Leavings ledger as a `Leaving` instead; and the
lease's own scratch directory is removed wholesale. `RestoreRecord`s are grouped by resolved path
first (`_group_by_path`): a path this lease touched more than once collapses into one outcome, so
noting the same path persisted twice in one lease -- or a mixed `persist=False` then `persist=True`
write to the same path, in either order -- never produces two Leaving rows or a lost restore
(coordinator review of this step's first cut found both). This module also defines
`kill_process_tree`, the one piece of process-killing logic both this releaser and
`hivemind.cell.local.session.LocalProcessSession`'s own watchdog and `close()` share, since "kill
the process group led by this pid" is the same operation whether it happens because a command
went over its quota or because the whole lease is being released.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Implements
    `hivemind.cell.lease.LeaseReleaser`; constructed by
    `hivemind.cell.local.source.HiveStandSource.lease` and injected into the `RealCellLease` it
    builds. Calls into the standard library (`os`, `signal`, `asyncio`, `shutil`, `hashlib`, on
    Windows `asyncio.create_subprocess_exec("taskkill", ...)`), `hivemind.cell.lease`/`.errors`/
    `.leavings`/`.source` (`CellIdentity`), `hivemind.common.errors` and `hivemind.pheromone`
    (`CellEvent`) only.

Key invariants:
    - `release()` always attempts every step (kill, replay-or-ledger, remove) even when an
      earlier one finds nothing to do; a path that cannot be restored is added to
      `residual_paths` rather than raising, so one bad path never stops the rest of the cleanup.
    - Every path is resolved to exactly one outcome, never one per `RestoreRecord`: if any record
      noted for a path has `persist=True`, the whole path is left -- using that group's *earliest*
      record's `prior` (what the path held before this lease touched it at all, roadmap step 5.0a
      bug fix) and its *last* `persist=True` record's `approved_by`/`reason` -- and no record for
      that path is ever restored. Otherwise the path is restored once, to its earliest record's
      `prior`.
    - A left path's content is read back from disk once, after every restore has already run, to
      build the `Leaving` `LeavingsStore.record_leaving` writes (atomic with its own `cell.left`
      event); landing in `LeaseReleaseReport.left_paths`, never `residual_paths` (leaving it is
      not a failure). `LeavingsStore.record_leaving` itself upserts rather than raising when an
      active row already exists (a second goal run leaving the same path while an earlier run's
      row is still active), always keeping that row's own original `prior`.
    - A left path whose content has vanished by release time is restored to its earliest record's
      `prior` like an ordinary non-persisted path, *unless* that `prior` is itself `None` (nothing
      was there before this lease touched it either): then the Cell already looks exactly as
      found, so the path is neither residual nor a Leaving.
    - `kill_process_tree` never raises for a pid that is already dead; it returns `False` instead.
    - Removing scratch retries briefly (`_SCRATCH_REMOVE_ATTEMPTS`) past an `OSError`: a process
      just killed mid-write can hold a file handle open for a beat after the OS reports it dead.

See Also:
    - .claude/roadmap.md step 3.11 for "release() terminates survivors and removes the directory."
    - .claude/roadmap.md step 5.0a for the Leavings ledger this releaser now writes to.
    - .claude/codingrules.md section 8.7 for "Owned versus leased" and what release() restores.
    - hivemind.cell.lease for LeaseReleaser, RestoreRecord and LeaseReleaseReport.
    - hivemind.cell.leavings for Leaving and LeavingsStore, the ledger this module writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell.lease import LeaseReleaseReport, RealCellLease, RestoreRecord
from hivemind.cell.leavings import ApprovedBy, Leaving, LeavingsStore
from hivemind.cell.source import CellIdentity
from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone import CellEvent
from waggle.clock import Clock
from waggle.ids import new_event_id

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

    def __init__(
        self,
        clock: Clock,
        leavings: LeavingsStore,
        identity: CellIdentity,
        *,
        keep_scratch: bool = False,
    ) -> None:
        """Build a releaser that times its SIGTERM-then-SIGKILL grace period from `clock`.

        Args:
            clock: Source of the grace-period wait between SIGTERM and SIGKILL on POSIX.
            leavings: Where every `persist=True` restore record's Leaving row lands (roadmap
                step 5.0a), atomic with its own `cell.left` trail event.
            identity: The Hive, node and actor this releaser stamps on every `cell.left` event;
                the same `CellIdentity` the lease itself was built with.
            keep_scratch: Development only (`[hive_stand] keep_scratch`): leave the scratch
                directory in place so a run's files can be read afterwards. The report then
                says so honestly: the directory is residual and the Cell is not restored.
        """
        self._clock = clock
        self._leavings = leavings
        self._identity = identity
        self._keep_scratch = keep_scratch

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Kill what `lease` started, restore what it touched, and remove its scratch directory.

        Args:
            lease: The lease being released, already transitioned to RELEASING.

        Returns:
            How many processes were killed, which paths (if any) could not be restored or
            removed, which paths were left in place under a Leaving, and whether the Hive Stand
            was left exactly as found (plus exactly those left paths).
        """
        killed = 0
        for pid in lease.started_pids:
            if await kill_process_tree(pid, self._clock):
                killed += 1
        outcome = await asyncio.to_thread(_replay_restore_records, lease.restore_records)
        # Ledgering happens back on this coroutine (LeavingsStore.record_leaving is async), after
        # the blocking replay/read work above but before scratch removal, so a Leaving is on
        # record before the report can claim the lease is released.
        await self._record_leavings(lease, outcome.left)
        # Kept on purpose counts as not removed: the report never claims a restore it skipped.
        scratch_removed = not self._keep_scratch and await asyncio.to_thread(
            _remove_scratch, lease.scratch_root
        )
        residual = outcome.residual
        if not scratch_removed:
            residual = (*residual, lease.scratch_root)
        return LeaseReleaseReport(
            killed_processes=killed,
            residual_paths=residual,
            left_paths=tuple(entry.path for entry in outcome.left),
            is_restored=scratch_removed and not residual,
        )

    async def _record_leavings(self, lease: RealCellLease, entries: tuple[_LeftEntry, ...]) -> None:
        """Write one Leaving row plus its `cell.left` event per left path.

        `LeavingsStore.record_leaving` upserts rather than raising on an active duplicate (its
        own docstring), so noting the same path across two leases -- the ordinary "run the same
        goal twice" case -- never fails release() here.
        """
        for entry in entries:
            leaving = Leaving(
                cell_id=lease.cell_id,
                path=entry.path,
                sha256=entry.sha256,
                size=entry.size,
                task_id=lease.task_id,
                lease_id=lease.id,
                approved_by=entry.approved_by,
                reason=entry.reason,
                prior=entry.prior,
                left_at=self._clock.now(),
                removed_at=None,
            )
            event = CellEvent(
                id=new_event_id(self._clock),
                hive_id=self._identity.hive_id,
                node_id=self._identity.node_id,
                at=self._clock.now(),
                actor=self._identity.actor,
                kind="cell.left",
                subject_id=lease.cell_id,
                payload={"lease_id": lease.id, "path": str(entry.path), "size": entry.size},
            )
            await self._leavings.record_leaving(leaving, event)


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
# Restoring paths, reading what stays, and removing scratch
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _LeftEntry:
    """One path release() ledgers as a Leaving: its own prior, approver, reason and content hash.

    Built from a *group* of same-path `RestoreRecord`s (`_group_by_path`), never straight from
    one record: `prior` is always the group's earliest record's own `prior` -- what the path held
    before this lease touched it at all -- and `approved_by`/`reason` come from the group's last
    `persist=True` record, so noting the same path persisted more than once in one lease still
    produces exactly one row.
    """

    path: Path
    prior: bytes | None
    approved_by: ApprovedBy
    reason: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class _ReplayOutcome:
    """What `_replay_restore_records` found: unrestorable paths, and paths left on purpose."""

    residual: tuple[Path, ...]
    left: tuple[_LeftEntry, ...]


def _replay_restore_records(records: tuple[RestoreRecord, ...]) -> _ReplayOutcome:
    """Restore every path this lease touched outside scratch, or ledger it as left.

    Groups `records` by resolved path first (`_group_by_path`), so a path noted more than once in
    this lease -- persisted twice, or a `persist=False` write followed (in either order) by a
    `persist=True` one -- resolves to exactly one outcome: restore, or leave.

    Args:
        records: `RealCellLease.restore_records`, in the order they were recorded.

    Returns:
        The paths that could not be restored, and the paths left as Leavings.
    """
    residual: list[Path] = []
    left: list[_LeftEntry] = []
    for path, group in _group_by_path(records).items():
        earliest = group[0]  # This lease's first write to `path`: its own true "left as found".
        persisted = [record for record in group if record.persist]
        if not persisted:
            if not _restore(path, earliest.prior):
                residual.append(path)
            continue
        entry = _build_left_entry(path, earliest.prior, persisted[-1])
        if entry is not None:
            left.append(entry)
        elif earliest.prior is not None and not _restore(path, earliest.prior):
            # Coordinator review: a persisted path that has vanished by release time is only
            # "as found" when nothing was there before this lease touched it either (the `elif`
            # above); otherwise it is restored like any ordinary path, never silently dropped.
            residual.append(path)
    return _ReplayOutcome(residual=tuple(residual), left=tuple(left))


def _group_by_path(records: tuple[RestoreRecord, ...]) -> dict[Path, list[RestoreRecord]]:
    """Group `records` by resolved path, each group kept in its own original recording order."""
    groups: dict[Path, list[RestoreRecord]] = {}
    for record in records:
        groups.setdefault(record.path, []).append(record)
    return groups


def _build_left_entry(path: Path, prior: bytes | None, chosen: RestoreRecord) -> _LeftEntry | None:
    """Read `path`'s current content and build its `_LeftEntry`, or None if it cannot be read.

    Args:
        path: The resolved path at least one record in its group noted with `persist=True`.
        prior: The path's group's earliest `prior` (what it held before this lease touched it).
        chosen: The group's last `persist=True` record, whose `approved_by`/`reason` this uses.

    Returns:
        A `_LeftEntry`, or None when `path` can no longer be read (typically: it is gone). The
        caller decides what "gone" means from here: `_replay_restore_records` falls back to an
        ordinary restore of `prior` rather than treating a vanished path as always "as found".
    """
    if chosen.approved_by is None or not chosen.reason:
        # RestoreRecord's own validator requires both, set together with persist=True; reaching
        # here means that validator was bypassed, a bug in the caller, not a normal outcome.
        raise InvariantViolationError(
            f"persist=True RestoreRecord for {path} has no approved_by/reason; RestoreRecord's "
            "own validator should have rejected this."
        )
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return _LeftEntry(
        path=path,
        prior=prior,
        approved_by=chosen.approved_by,
        reason=chosen.reason,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def _restore(path: Path, prior: bytes | None) -> bool:
    """Write `prior` back at `path`, or unlink it when `prior` is None; return whether it worked."""
    try:
        if prior is None:
            path.unlink(missing_ok=True)  # It did not exist before this lease touched it.
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(prior)
    except OSError:
        return False
    return True


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
