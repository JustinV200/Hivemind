"""Collect log lines and file changes inside named roots: the file half of watch mode.

A Warden (the per-Cell supervisor) watching an idle Real Cell (an existing device the Hive
borrows for a task) may ask its Pollen Packet (the thin gateway program on the device) for the
tail of the log files under some roots and for the files created, changed or deleted there since a
cursor (ADR-0054). Both are fixed collectors that read the file system directly and never run a
command. The roots were already checked against ``fs:read`` by the time they get here. The walk
never follows a symlink, so a link inside a root cannot lead the collector out of it; it is
bounded in entries and depth, so a huge tree cannot stall the device; a path longer than the wire
allows is skipped (a cut path would name another file) and the answer says it is truncated. File
changes are found by comparing the root's listing with the one taken last time, which also finds
deletions; the first look at a root can only say which files are recent.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by
    pollen.executors.observe.executor, which owns the one ``ChangeWatcher``; calls into waggle
    value models and the standard library only.

Key invariants:
    - Read-only: nothing here writes, renames or deletes.
    - No symlink is followed or read, at any depth.
    - Every list is cut at the bound the wire model allows and the cut is reported.
    - Changes are returned oldest first, so the last one's time is the next cursor with nothing
      missed.

See Also:
    - docs/waggle/gateway-contract.md section 9.4 (observation) and ADR-0054.
    - pollen.executors.observe.system for the process and resource half.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from waggle.messages.swarm import FileChange, FileChangeKind, LogLine
from waggle.messages.swarm.watch import (
    MAX_LOG_LINE_CHARS,
    MAX_LOG_LINES,
    MAX_OBSERVED_PATH_CHARS,
)

MAX_SCAN_ENTRIES = 10_000  # Directory entries one root's walk visits before it stops.
MAX_SCAN_DEPTH = 6  # Directory levels below a root; deeper trees are not logs or recent work.
MAX_LOG_FILES = 20  # The newest log files read per observation.
TAIL_BYTES = 65_536  # How much of the end of a log file is read.
RECENT_WINDOW_S = 300.0  # The first look at a root reports files changed in the last five minutes.
MAX_SNAPSHOT_ROOTS = 32  # Roots whose last listing is remembered; the oldest is forgotten first.

type Listing = dict[str, tuple[float, int]]  # Path -> (modification time, size in bytes).

__all__ = ["ChangeWatcher", "Scan", "scan_root", "tail_logs"]


@dataclass(frozen=True, slots=True)
class Scan:
    """One root's regular files, and whether the walk was cut short."""

    files: Listing  # Every regular file found, symlinks excluded.
    is_truncated: bool  # The walk hit its entry bound.


def scan_root(root: Path) -> Scan:
    """List the regular files under ``root`` without following a symlink.

    Args:
        root: A directory already checked against the read capability.

    Returns:
        The files with their modification times and sizes; a missing root is empty.
    """
    files: Listing = {}
    pending = [(root, 0)]
    visited = 0
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_SCAN_ENTRIES:
                        return Scan(files, True)
                    if entry.is_dir(follow_symlinks=False) and depth < MAX_SCAN_DEPTH:
                        pending.append((Path(entry.path), depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        status = entry.stat(follow_symlinks=False)
                        files[entry.path] = (status.st_mtime, status.st_size)
        except OSError:
            continue  # Removed or unreadable while walking: nothing there to report.
    return Scan(files, False)


def tail_logs(roots: Sequence[Path], cap: int) -> tuple[tuple[LogLine, ...], bool]:
    """Read the newest lines of the newest ``.log`` files under ``roots``.

    Args:
        roots: Directories already checked against the read capability.
        cap: The most lines to return; also capped at the wire bound.

    Returns:
        The lines, oldest first, and whether anything was left out.
    """
    cap = min(cap, MAX_LOG_LINES)
    logs: list[tuple[float, str]] = []
    truncated = False
    for root in roots:
        scan = scan_root(root)
        truncated = truncated or scan.is_truncated
        logs.extend(
            (mtime, path) for path, (mtime, size) in scan.files.items() if _is_log(path, size)
        )
    logs.sort()  # Oldest file first, so lines come out in roughly the order they were written.
    if len(logs) > MAX_LOG_FILES:
        logs, truncated = logs[-MAX_LOG_FILES:], True
    lines: list[LogLine] = []
    for _, path in logs:
        if len(path) > MAX_OBSERVED_PATH_CHARS:
            truncated = True  # A cut path would name another file, so the file is skipped.
            continue
        tail, has_more = _tail_lines(Path(path), cap)
        truncated = truncated or has_more
        lines.extend(LogLine(path=path, line=line) for line in tail)
    return tuple(lines[-cap:]), truncated or len(lines) > cap


def _is_log(path: str, size: int) -> bool:
    """Return whether ``path`` is a non-empty ``.log`` file."""
    return size > 0 and path.endswith(".log")


def _tail_lines(path: Path, count: int) -> tuple[list[str], bool]:
    """Return a file's last ``count`` lines (each cut at the wire bound) and if more were read."""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return [], False
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]  # The first line of a mid-file read is a fragment.
    return [line[:MAX_LOG_LINE_CHARS] for line in lines[-count:]], len(lines) > count


class ChangeWatcher:
    """Finds the files that changed under a root since the last time it looked.

    Owns mutable state (codingrules 8.5, documented): ``_listings`` remembers each root's last
    listing so the next look can tell created from modified and can see deletions.
    """

    def __init__(self) -> None:
        """Start with no listing, so the first look at a root reports recent files only."""
        self._listings: dict[str, Listing] = {}

    def changes(
        self, roots: Sequence[Path], since_at: datetime | None, cap: int
    ) -> tuple[tuple[FileChange, ...], bool]:
        """Report the changes under ``roots`` after the cursor, oldest first.

        Args:
            roots: Directories already checked against the read capability.
            since_at: The cursor; None asks for the packet's own recent window.
            cap: The most changes to return; the oldest are kept so the cursor loses nothing.

        Returns:
            The changes and whether anything was left out.
        """
        now = datetime.now(UTC)
        found: list[FileChange] = []
        truncated = False
        for root in roots:
            scan = scan_root(root)
            # A path over the wire bound is left out of the listing altogether: a cut path would
            # name another file, and a change model cannot even hold it.
            listing = {p: v for p, v in scan.files.items() if len(p) <= MAX_OBSERVED_PATH_CHARS}
            truncated = truncated or scan.is_truncated or len(listing) < len(scan.files)
            found.extend(_diff(self._listings.get(str(root)), listing, now, since_at))
            self._remember(str(root), listing)
        found.sort(key=lambda change: change.at)
        return tuple(found[:cap]), truncated or len(found) > cap

    def _remember(self, root: str, listing: Listing) -> None:
        """Keep ``listing`` as the root's last look, forgetting the oldest root past the bound."""
        self._listings.pop(root, None)
        self._listings[root] = listing
        while len(self._listings) > MAX_SNAPSHOT_ROOTS:
            del self._listings[next(iter(self._listings))]


def _diff(
    previous: Listing | None, current: Listing, now: datetime, since_at: datetime | None
) -> list[FileChange]:
    """Compare two listings of one root; every change is stamped and filtered by the cursor."""
    # With a cursor, only later changes count. Without one, a root already seen reports every
    # difference, and a root seen for the first time reports what changed in the recent window.
    threshold = since_at
    if threshold is None and previous is None:
        threshold = now - timedelta(seconds=RECENT_WINDOW_S)
    changes: list[FileChange] = []
    for path, (mtime, size) in current.items():
        at = datetime.fromtimestamp(mtime, UTC)
        if previous is None:
            kind = FileChangeKind.MODIFIED  # The first look cannot tell created from modified.
        elif path not in previous:
            kind = FileChangeKind.CREATED
        elif previous[path] != (mtime, size):
            kind = FileChangeKind.MODIFIED
        else:
            continue
        if threshold is None or at > threshold:
            changes.append(FileChange(path=path, change=kind, at=at, size_bytes=size))
    for path in () if previous is None else previous.keys() - current.keys():
        changes.append(
            FileChange(path=path, change=FileChangeKind.DELETED, at=now, size_bytes=None)
        )
    return changes
