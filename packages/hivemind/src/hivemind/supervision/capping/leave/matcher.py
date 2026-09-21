"""Define matches_leaving: match a resolved path against a task's declared `leaves` patterns.

Roadmap step 5.0c: "You need a pure matcher (pattern against a concrete resolved path, `~`
expanded against the Cell's home, Windows case-insensitivity via `PureWindowsPath`); decide the
glob semantics conservatively (exact path, or a directory pattern covering what is under it, plus
`*`/`**` if cheap) and document them in the leave package README." `PlannedLeaving.pattern`
(`waggle.messages.PlannedLeaving`, roadmap step 5.0b) is already known well-formed -- absolute or
`~`-rooted, never a bare root/drive/home, never a `..` segment -- by its own validator before it
ever reaches here.

Glob semantics (documented in full in this package's README, "Matcher semantics"):
    - No `*` in the pattern: the pattern covers itself and everything nested under it (a directory
      declaration covers what a task writes inside it without enumerating every file); an exact
      file pattern only ever "covers" itself, since nothing can be nested under a file.
    - `*` matches any run of characters within one path segment; `**` matches any run of
      characters including separators (so it can span segments). Translated to a regex once per
      call (cheap: a `leaves` tuple is at most `MAX_LEAVES_ITEMS`, 16 entries).
    - `~` expands against `home` textually, before either rule above is applied, so a pattern like
      `~/Projects/**/*.log` expands to `<home>/Projects/**/*.log` and is then glob-matched.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Called by `hivemind.supervision.capping.apply` once per outside-scratch path, to
    compute `decide`'s own `declared` argument. Calls into `waggle.messages` (PlannedLeaving) and
    `hivemind.cell` (OsFamily) and the standard library only.

Key invariants:
    - Comparison always goes through `PureWindowsPath`/`PurePosixPath`, chosen by `os_family`
      (mirrors `hivemind.supervision.capping.leave.classify`'s own convention), so Windows
      comparisons are case-insensitive "for free" (pathlib's own behaviour) with no extra
      lower-casing needed there; POSIX patterns stay case-sensitive, matching POSIX filesystems.
    - The first declared pattern that covers `path` wins (`declared` order, the plan's own
      declaration order); a caller that needs the *most specific* match sorts `declared` itself.
    - Never does filesystem I/O: "covers" is decided from the two path strings alone.

See Also:
    - .claude/roadmap.md step 5.0c for this module's own requirement, verbatim.
    - hivemind.supervision.capping.leave.README for the full "Matcher semantics" writeup.
    - waggle.messages.labels for PlannedLeaving and its own pattern validator.
    - hivemind.supervision.capping.leave.classify for the sibling PureWindowsPath/PurePosixPath
      convention this module follows.
"""

from __future__ import annotations

import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from hivemind.cell import OsFamily
from waggle.messages import PlannedLeaving

__all__ = ["matches_leaving"]


def matches_leaving(
    path: str, declared: tuple[PlannedLeaving, ...], os_family: OsFamily, home: str
) -> PlannedLeaving | None:
    """Return the first declared PlannedLeaving whose pattern covers `path`, or None.

    Args:
        path: The resolved, absolute path as text, in `os_family`'s own separator style.
        declared: The task's own `leaves`, in declaration order.
        os_family: Which OS the owning Cell runs; chooses the pure-path flavour.
        home: The Cell's own home directory, as text, for `~`-rooted pattern expansion.

    Returns:
        The first `declared` entry that covers `path` (module docstring's own glob semantics), or
        None when nothing declared covers it -- the roadmap 5.0b hard rule's own signal that
        `hivemind.supervision.capping.leave.policy.decide` must be called with `declared=False`.
    """
    flavor = _flavor(os_family)
    candidate = flavor(path)
    for leaving in declared:
        pattern_text = _expand_home(leaving.pattern, home)
        if _covers(candidate, pattern_text, flavor):
            return leaving
    return None


def _flavor(os_family: OsFamily) -> type[PurePath]:
    """Return the PurePath subclass matching `os_family` (mirrors classify.py's own helper)."""
    return PureWindowsPath if os_family is OsFamily.WINDOWS else PurePosixPath


def _expand_home(pattern: str, home: str) -> str:
    """Expand a leading `~` in `pattern` against `home`, textually, before any glob parsing."""
    if not pattern.startswith("~"):
        return pattern
    rest = pattern[1:].lstrip("/\\")
    if not rest:
        return home  # PlannedLeaving's own validator already refuses a bare "~" pattern.
    separator = "\\" if "\\" in home and "/" not in home else "/"
    return f"{home.rstrip('/\\')}{separator}{rest}"


def _covers(candidate: PurePath, pattern_text: str, flavor: type[PurePath]) -> bool:
    """Return whether the (already `~`-expanded) pattern covers `candidate` (module docstring)."""
    if "*" in pattern_text:
        return _glob_match(candidate, pattern_text, windows=flavor is PureWindowsPath)
    pattern_path = flavor(pattern_text)
    return candidate == pattern_path or pattern_path in candidate.parents


def _glob_match(candidate: PurePath, pattern_text: str, *, windows: bool) -> bool:
    """Match `candidate` against a `*`/`**` glob pattern (module docstring's own semantics)."""
    # as_posix() normalises both sides to forward slashes first (PureWindowsPath.as_posix() turns
    # "\\" into "/" without touching the "*"/"**" wildcards themselves), so one regex translation
    # below handles either family; Windows then also compares case-insensitively.
    flavor = PureWindowsPath if windows else PurePosixPath
    candidate_text = candidate.as_posix()
    pattern_text = flavor(pattern_text).as_posix()
    if windows:
        candidate_text = candidate_text.lower()
        pattern_text = pattern_text.lower()
    return re.fullmatch(_glob_to_regex(pattern_text), candidate_text) is not None


def _glob_to_regex(pattern: str) -> str:
    """Translate a POSIX-separator glob into a regex: `**` spans separators, `*` does not."""
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i : i + 2] == "**":
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return "".join(parts)
