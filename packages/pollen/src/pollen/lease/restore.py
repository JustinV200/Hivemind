"""Record what a lease changes outside scratch, and put it all back on release.

A Real Cell is an existing device the Hive borrows for a task and leaves exactly as found. A
lease (the Hive's temporary hold on it) may be allowed to write outside its scratch directory, but
nothing there may change until the Pollen Packet (the thin gateway program on the device) has
recorded what the path held before: that it did not exist (a file, or a directory the write needs
created), or its bytes and mode, copied into a backup directory of the lease's own. The record is
written to the lease table first; only then does the change happen, so a crash can never leave a
change nobody can undo (gateway-contract section 12). A release replays the records in reverse
order of recording (delete what the lease created, write back what it changed), removes the
scratch directory and the backups, and reports any path it could not restore.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by the file and
    process executors before a write outside scratch, and by pollen.agent.leases on release;
    changes the lease table through pollen.lease.table; calls into the standard library only.

Key invariants:
    - A path is recorded before it is changed, once: the first record carries the earliest prior
      state, which is the one a release puts back.
    - A prior state that cannot be recorded (too large, no room) refuses the change; nothing is
      ever changed outside scratch unrecorded.
    - A restore step that fails leaves its path in the residual list and never stops the rest.

See Also:
    - docs/waggle/gateway-contract.md section 12 (paths, scratch and left-as-found).
    - packages/hivemind/src/hivemind/cell/local/releaser.py for the Hive Stand's equivalent.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path

from pollen.errors import PathRefusedError, QuotaExceededError
from pollen.lease.lease import DeviceLease, RestoreKind, RestoreRecord
from pollen.lease.table import LeaseTable
from pollen.state import PRIVATE_DIR_MODE, DataLayout

MAX_BACKUP_BYTES = 67_108_864  # 64 MiB: the largest prior file a lease may overwrite and undo.
MAX_RESTORE_RECORDS = (
    1_024  # Paths one lease may change outside scratch; more means scratch is misplaced.
)
SCRATCH_REMOVE_ATTEMPTS = 5  # A just-killed process can hold a handle open for a beat.
SCRATCH_REMOVE_RETRY_S = 0.05  # So five tries 50 ms apart bound the wait at a quarter second.

__all__ = [
    "MAX_BACKUP_BYTES",
    "MAX_RESTORE_RECORDS",
    "record_before_change",
    "remove_scratch",
    "replay_restores",
]


async def record_before_change(
    table: LeaseTable, layout: DataLayout, lease: DeviceLease, target: Path
) -> None:
    """Record ``target`` (and any directory its write must create) before it is changed.

    Args:
        table: The lease table the records are written to.
        layout: Where the lease's backup directory is.
        lease: The lease making the change, as currently stored.
        target: The resolved absolute path about to be written, replaced or deleted.

    Raises:
        PathRefusedError: ``target`` is a directory.
        QuotaExceededError: The prior state is too large to record, or there is no room to.
        StateError: The lease table could not be written.
    """
    records = await asyncio.to_thread(_plan, lease, layout.backups(lease.lease_id), target)
    # Persisted before the caller changes anything: the table write is what makes it safe to.
    if records:
        await table.modify(lease.lease_id, lambda stored: stored.with_restore(records))


def _plan(lease: DeviceLease, backups: Path, target: Path) -> tuple[RestoreRecord, ...]:
    """Work out the records ``target`` needs, copying its prior bytes aside (blocking)."""
    if lease.is_recorded(str(target)):
        return ()  # Changed before in this lease: the first record holds the earliest prior state.
    records = [
        RestoreRecord(path=str(directory), kind=RestoreKind.CREATED_DIR)
        for directory in _missing_directories(target)
    ]
    if len(lease.restores) + len(records) + 1 > MAX_RESTORE_RECORDS:
        raise QuotaExceededError(
            f"Lease {lease.lease_id} already changed {len(lease.restores)} paths outside "
            "scratch; no more can be recorded, so the change was refused."
        )
    records.append(_record_file(lease, backups, target, index=len(lease.restores) + len(records)))
    return tuple(records)


def _missing_directories(target: Path) -> list[Path]:
    """Return the ancestors of ``target`` that do not exist yet, outermost first."""
    missing: list[Path] = []
    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        missing.append(parent)
        parent = parent.parent
    return missing[::-1]


def _record_file(lease: DeviceLease, backups: Path, target: Path, index: int) -> RestoreRecord:
    """Build the record for ``target`` itself, backing its bytes up when it exists."""
    try:
        status = target.stat()
    except FileNotFoundError:
        return RestoreRecord(path=str(target), kind=RestoreKind.CREATED_FILE)
    except OSError as exc:
        raise QuotaExceededError(
            f"The prior state of a path for lease {lease.lease_id} could not be read, so the "
            "change was refused."
        ) from exc
    if stat.S_ISDIR(status.st_mode):
        raise PathRefusedError(f"A path in a request for lease {lease.lease_id} is a directory.")
    if status.st_size > MAX_BACKUP_BYTES:
        raise QuotaExceededError(
            f"A file lease {lease.lease_id} would replace is {status.st_size} bytes, over the "
            f"{MAX_BACKUP_BYTES} the packet can record and restore, so the change was refused."
        )
    name = f"{index}.bak"
    try:
        backups.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        shutil.copyfile(target, backups / name)
    except OSError as exc:
        raise QuotaExceededError(
            f"The prior bytes of a path for lease {lease.lease_id} could not be saved, so the "
            "change was refused."
        ) from exc
    return RestoreRecord(
        path=str(target), kind=RestoreKind.REPLACED, backup=name, mode=stat.S_IMODE(status.st_mode)
    )


def replay_restores(layout: DataLayout, lease: DeviceLease) -> list[str]:
    """Undo every recorded change, newest first, and remove the backups (blocking).

    Args:
        layout: Where the lease's backup directory is.
        lease: The lease being released.

    Returns:
        The paths that could not be restored; empty when the device is as it was found.
    """
    backups = layout.backups(lease.lease_id)
    residual: list[str] = []
    for record in reversed(lease.restores):
        try:
            _restore_one(record, backups)
        except OSError:
            residual.append(record.path)
    # Backups go only when every path is back: a failed restore keeps its bytes for the retry.
    if not residual:
        shutil.rmtree(backups, ignore_errors=True)
    return residual


def _restore_one(record: RestoreRecord, backups: Path) -> None:
    """Put one recorded path back; raise OSError when it cannot be."""
    path = Path(record.path)
    if record.kind is RestoreKind.CREATED_FILE:
        path.unlink(missing_ok=True)
    elif record.kind is RestoreKind.CREATED_DIR:
        try:
            path.rmdir()
        except FileNotFoundError:
            return  # Already gone: nothing to undo.
    elif record.backup is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backups / record.backup, path)
        if record.mode is not None:
            os.chmod(path, record.mode)


def remove_scratch(root: Path) -> bool:
    """Remove a lease's scratch directory wholesale, retrying briefly (blocking).

    Args:
        root: The lease's scratch directory.

    Returns:
        True once it no longer exists (including if it never did); False if every attempt failed.
    """
    for _ in range(SCRATCH_REMOVE_ATTEMPTS):
        try:
            if root.is_symlink():
                root.unlink()  # A link in the root's place: remove the link, never what it names.
            elif root.exists():
                shutil.rmtree(root, onexc=_make_writable)
        except OSError:
            # A just-killed process can still hold a handle for a beat; wait, then try again.
            time.sleep(SCRATCH_REMOVE_RETRY_S)
            continue
        return True
    return not root.exists()


def _make_writable(function: Callable[..., object], path: str, error: BaseException) -> None:
    """Retry a failed removal once after making ``path`` writable (read-only files, Windows)."""
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IRWXU)
    function(path)
