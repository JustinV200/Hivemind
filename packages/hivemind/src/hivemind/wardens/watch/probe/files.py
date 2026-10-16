"""Parse what the probe's scripts printed about files into log lines and file changes.

A Warden watching a Cell it is local to lists the regular files under the operator's roots and
reads the end of the newest `.log` files among them, both through its own session
(`hivemind.wardens.watch.probe.commands`). This module turns that text into the same `LogLine` and
`FileChange` values a Pollen Packet sends, with the same bounds and the same rules, so the observer,
the signals and the Patrol never know which probe produced a snapshot. The parsers are pure and
total: a record that is malformed, or whose path the wire cannot hold, is skipped (a cut path would
name another file) and the result says it is truncated. `ChangeTracker` is the one stateful part: it
remembers each root's last listing so the next look can tell a created file from a modified one and
can see deletions; the first look at a root can only say which files are recent. What it remembers
of a look is only what that look reported: a change the cap left out stays a difference, and comes
out on the next look, so a burst of changes is delivered in full over several looks.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch.probe sub-package. Used by `hivemind.wardens.watch.probe.session.SessionWatchProbe`
    only. Calls into `hivemind.wardens.watch.probe.commands` and waggle's swarm messages only.

Key invariants:
    - Nothing here raises for malformed input; it returns less.
    - Every list is cut at the bound the wire model allows, and the cut is reported.
    - Changes are returned oldest first, so the last one's time is the next cursor with nothing
      missed; a change the cap left out is still a difference on the next look.
    - A listing that is absent is not an empty one: the tracker is only given the roots that were
      actually listed, so a failed sample can never read as every file deleted.

See Also:
    - pollen.executors.observe.files for the packet's own collectors, whose rules these match.
    - waggle.messages.swarm.watch for the value models and their bounds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hivemind.wardens.watch.probe.commands import (
    MAX_LOG_FILES,
    RECENT_WINDOW_S,
    TAIL_BYTES,
    TAIL_LINES,
)
from waggle.messages.swarm import FileChange, FileChangeKind, LogLine
from waggle.messages.swarm.watch import (
    MAX_LOG_LINE_CHARS,
    MAX_LOG_LINES,
    MAX_OBSERVED_PATH_CHARS,
)

MAX_LISTED_FILES = 10_000  # Files one root's listing keeps; one more proves the root has more.
MAX_SNAPSHOT_ROOTS = 32  # Roots whose last listing is remembered; the oldest is forgotten first.
_MAX_EPOCH = 253_402_300_799.0  # 9999-12-31: a later modification time is a clock gone wrong.
_REPLACEMENT = "�"  # What decoding puts where a path held bytes that are not text.

type Listing = dict[str, tuple[float, int]]  # Path -> (modification time, size in bytes).

__all__ = [
    "MAX_LISTED_FILES",
    "ChangeTracker",
    "Listing",
    "RootListing",
    "newest_logs",
    "parse_listing",
    "parse_roots",
    "parse_tail",
]


@dataclass(frozen=True, slots=True)
class RootListing:
    """One root's regular files, and whether the listing was cut short."""

    files: Listing  # Every regular file found, symlinks excluded.
    is_truncated: bool  # The output hit a bound, or a path could not be carried.


def parse_roots(sections: Mapping[str, str], roots: Sequence[str]) -> dict[str, RootListing]:
    """Read the listing of each root that the script printed one for.

    Args:
        sections: The script's sections, from `split_sections`.
        roots: The roots the script was given, in order; the n-th is the section `root<n>`.

    Returns:
        Root to its listing, only for roots whose section is present: one whose section is missing
        (the script was killed first) was not looked at, which is not the same as being empty.
    """
    listings: dict[str, RootListing] = {}
    for position, root in enumerate(roots, start=1):
        text = sections.get(f"root{position}")
        if text is not None:
            listings.setdefault(root, parse_listing(text))
    return listings


def parse_listing(text: str) -> RootListing:
    """Read one root's NUL-terminated `mtime<TAB>size<TAB>path` records.

    Args:
        text: The root's section.

    Returns:
        Its regular files with modification time and size. A record cut off at the end of the
        output, one that is malformed, and a path over the wire bound or holding bytes that are not
        text are left out, and the last two (and the cut) mark the listing truncated.
    """
    *records, rest = text.split("\0")
    is_truncated = bool(rest.strip())  # Output ended in the middle of a record: head cut it.
    files: Listing = {}
    for record in records:
        parsed = _parse_record(record)
        if parsed is None:
            continue
        path, mtime, size = parsed
        if len(path) > MAX_OBSERVED_PATH_CHARS or _REPLACEMENT in path:
            is_truncated = True  # A cut or repaired path would name another file: skip it.
        elif len(files) >= MAX_LISTED_FILES:
            return RootListing(files, True)
        else:
            files[path] = (mtime, size)
    return RootListing(files, is_truncated)


def newest_logs(listings: Iterable[RootListing]) -> tuple[list[tuple[str, int]], bool]:
    """Pick the newest non-empty `.log` files across the listings.

    Args:
        listings: Every root's listing.

    Returns:
        (path, size) of up to `MAX_LOG_FILES` files, oldest first so lines come out in roughly the
        order they were written, and whether older ones were left out.
    """
    logs: Listing = {}
    for listing in listings:
        for path, (mtime, size) in listing.files.items():
            if size > 0 and path.endswith(".log"):
                logs.setdefault(path, (mtime, size))  # A file under two nested roots counts once.
    ordered = sorted(logs.items(), key=lambda item: (item[1][0], item[0]))
    newest = ordered[-MAX_LOG_FILES:]
    return [(path, size) for path, (_, size) in newest], len(ordered) > len(newest)


def parse_tail(
    sections: Mapping[str, str], logs: Sequence[tuple[str, int]], cap: int
) -> tuple[tuple[LogLine, ...], bool]:
    """Read the tail script's output into log lines.

    Args:
        sections: The tail script's sections; the n-th file's text is the section `tail<n>`.
        logs: The (path, size) pairs the script was given, in order. Paths come from here, never
            from the output, which holds file contents and could say anything.
        cap: The most lines to return, also capped at the wire bound.

    Returns:
        The lines, oldest file first, and whether anything was left out.
    """
    cap = min(cap, MAX_LOG_LINES)
    lines: list[LogLine] = []
    is_truncated = False
    for position, (path, size) in enumerate(logs, start=1):
        tail = _tail_lines(sections.get(f"tail{position}", ""), size)
        is_truncated = is_truncated or len(tail) > TAIL_LINES
        lines.extend(LogLine(path=path, line=line) for line in tail[-TAIL_LINES:])
    return tuple(lines[-cap:]), is_truncated or len(lines) > cap


class ChangeTracker:
    """Finds the files that changed under a root since the last time it looked.

    Owns mutable state (codingrules 8.5, documented): `_listings` remembers each root's last
    listing so the next look can tell created from modified and can see deletions.
    """

    def __init__(self) -> None:
        """Start with no listing, so the first look at a root reports recent files only."""
        self._listings: dict[str, Listing] = {}

    def changes(
        self,
        listings: Mapping[str, RootListing],
        since_at: datetime | None,
        now: datetime,
        cap: int,
    ) -> tuple[tuple[FileChange, ...], bool]:
        """Report the changes under the listed roots after the cursor, oldest first.

        Args:
            listings: Root to this look's listing, for the roots that were actually listed.
            since_at: The cursor; None asks for the recent window (first look) or every
                difference from the last look.
            now: The instant of this look, which a deletion is stamped with.
            cap: The most changes to return; the oldest are kept so the cursor loses nothing.

        Returns:
            The changes and whether anything was left out.
        """
        found: dict[str, FileChange] = {}
        diffs: dict[str, list[FileChange]] = {}
        is_truncated = False
        for root, listing in listings.items():
            is_truncated = is_truncated or listing.is_truncated
            diffs[root] = _diff(self._listings.get(root), listing.files, now, since_at)
            for change in diffs[root]:
                found.setdefault(change.path, change)  # Nested roots report a file once.
        ordered = sorted(found.values(), key=lambda change: change.at)
        kept = tuple(ordered[:cap])
        # Remembered only as reported: a change the cap left out keeps the last look's view of
        # its file, so it is a difference again next time instead of being lost for good.
        reported = {change.path for change in kept}
        for root, listing in listings.items():
            left_out = [change for change in diffs[root] if change.path not in reported]
            self._remember(root, _carried(self._listings.get(root), listing.files, left_out))
        return kept, is_truncated or len(ordered) > cap

    def _remember(self, root: str, files: Listing) -> None:
        """Keep `files` as the root's last look, forgetting the oldest root past the bound."""
        self._listings.pop(root, None)
        self._listings[root] = files
        while len(self._listings) > MAX_SNAPSHOT_ROOTS:
            del self._listings[next(iter(self._listings))]


def _carried(previous: Listing | None, current: Listing, left_out: Iterable[FileChange]) -> Listing:
    """Return `current` with each change in `left_out` undone, as the last look still saw it.

    A file created or modified but not reported keeps its old entry (none, or the old time and
    size); one deleted but not reported keeps the entry it had. The next diff finds each again.
    """
    carried = dict(current)
    for change in left_out:
        before = previous.get(change.path) if previous is not None else None
        if before is None:
            carried.pop(change.path, None)
        else:
            carried[change.path] = before
    return carried


# ──────────────────────────────────────────────────────────────────────────────
# Record parsers
# ──────────────────────────────────────────────────────────────────────────────


def _parse_record(record: str) -> tuple[str, float, int] | None:
    """Parse one `mtime<TAB>size<TAB>path` record, or None when it is not one."""
    mtime_text, _, rest = record.partition("\t")
    size_text, _, path = rest.partition("\t")
    try:
        mtime, size = float(mtime_text), int(size_text)
    except ValueError:
        return None
    # A modification time that is not a date (NaN, infinity, the year 3000000) must not reach
    # datetime; an absolute path is what find prints for an absolute root.
    if not (math.isfinite(mtime) and 0 <= mtime < _MAX_EPOCH) or size < 0:
        return None
    return (path, mtime, size) if path.startswith("/") else None


def _tail_lines(text: str, size: int) -> list[str]:
    """Return a file's tail as lines, each cut at the wire bound, without blank ones."""
    lines = [line.rstrip("\r")[:MAX_LOG_LINE_CHARS] for line in text.split("\n")]
    if size > TAIL_BYTES:
        lines = lines[1:]  # The first line of a mid-file read is a fragment.
    # Blank lines carry nothing, and the cap counts lines, not bytes.
    return [line for line in lines if line.strip()]


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
    # Sorted, so deletions (all stamped `now`) come out in the same order on every run.
    for path in () if previous is None else sorted(previous.keys() - current.keys()):
        changes.append(
            FileChange(path=path, change=FileChangeKind.DELETED, at=now, size_bytes=None)
        )
    return changes
