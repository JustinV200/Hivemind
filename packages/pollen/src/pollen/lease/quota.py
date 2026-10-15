"""Measure and enforce a lease's scratch quota, the way the Hive Stand does for its own leases.

A Real Cell is an existing device the Hive borrows for a task, so a runaway or hostile command
must never fill the operator's disk. A lease (the Hive's temporary hold on the device) has a
scratch directory of its own and a quota in bytes, from the holder's Ceilings (the limits the
Queen, the Hive's central orchestrator, sets once on a Warden, the per-Cell supervisor) or the
packet's own default. The usage is measured by walking the directory without ever following a
symlink, so a link inside scratch can neither hide nor inflate what the lease holds; the exec
watchdog samples it while a command runs and kills the command's process group when it is
crossed, and a ``session.put_file`` that would cross it, or would leave the disk with no headroom,
is refused before a byte is written (gateway-contract section 9.1).

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by the exec watchdog
    in pollen.executors.session and by the put-file transfer in pollen.executors.files; calls
    into the standard library only.

Key invariants:
    - The walk never follows a symlink, at any depth, and counts regular files only.
    - A file counts what it holds now, still open or not: its size is asked of the file itself,
      never of the directory listing, which Windows brings up to date only when it is closed.
    - A directory that vanishes mid-walk (a command's own clean-up) counts as zero, never raises.
    - A refusal names the lease and the figures, never a path.

See Also:
    - packages/hivemind/src/hivemind/cell/local/quota.py for the Hive Stand's equivalent.
    - docs/waggle/gateway-contract.md section 9.1 for the quota rules.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pollen.errors import QuotaExceededError

DEFAULT_SCRATCH_QUOTA_BYTES = 536_870_912  # 512 MiB: roomy for a task, safe on a small SD card.
QUOTA_SAMPLE_INTERVAL_S = 0.25  # How often the watchdog re-measures while a command runs.
DISK_HEADROOM_BYTES = 67_108_864  # 64 MiB the device keeps free for itself after any write.

__all__ = [
    "DEFAULT_SCRATCH_QUOTA_BYTES",
    "DISK_HEADROOM_BYTES",
    "QUOTA_SAMPLE_INTERVAL_S",
    "check_room",
    "directory_size_bytes",
]


def directory_size_bytes(root: Path) -> int:
    """Sum the size of every regular file under ``root`` without following a symlink.

    Args:
        root: The directory to measure, normally a lease's scratch root.

    Returns:
        The total in bytes; a directory that does not exist, or vanishes partway, counts as zero
        for the part that is gone.
    """
    total = 0
    pending = [root]
    # An explicit stack, not recursion: a command can build a directory tree deeper than the
    # interpreter's recursion limit, and the walk must survive whatever scratch holds.
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    # follow_symlinks=False on both checks is the point of this function.
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += _size_now(entry)
        except OSError:
            continue  # Removed or unreadable mid-walk: nothing there weighs anything.
    return total


def _size_now(entry: os.DirEntry[str]) -> int:
    """Return what the regular file ``entry`` holds right now, or zero once it is gone."""
    # Not `entry.stat()`: on Windows that answers from the directory listing, which NTFS updates
    # only when the file is closed, so a command filling scratch through one open file would weigh
    # nothing until it stopped. `os.lstat` asks the file itself; on POSIX it is the very call
    # `entry.stat(follow_symlinks=False)` makes.
    try:
        return os.lstat(entry.path).st_size
    except OSError:
        return 0  # Removed between the listing and this look: it weighs nothing now.


def check_room(root: Path, quota_bytes: int, incoming: int, lease_id: str) -> None:
    """Refuse a write of ``incoming`` bytes that would cross the quota or fill the disk.

    Args:
        root: The lease's scratch directory.
        quota_bytes: The lease's quota.
        incoming: The bytes about to be written.
        lease_id: The lease, for the message.

    Raises:
        QuotaExceededError: The directory would pass its quota, or the disk would be left with
            less than DISK_HEADROOM_BYTES free.
    """
    used = directory_size_bytes(root)
    if used + incoming > quota_bytes:
        raise QuotaExceededError(
            f"Writing {incoming} bytes would take lease {lease_id}'s scratch from {used} bytes "
            f"past its quota of {quota_bytes} bytes."
        )
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return  # The disk cannot be measured here; the quota above still holds.
    if free < incoming + DISK_HEADROOM_BYTES:
        raise QuotaExceededError(
            f"Writing {incoming} bytes for lease {lease_id} would leave the device's disk with "
            f"less than {DISK_HEADROOM_BYTES} bytes free."
        )
