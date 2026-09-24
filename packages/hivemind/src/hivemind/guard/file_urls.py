"""Decide whether a file URL stays inside the directories a lease lets its browser read.

A browser the Exoskeleton (a Worker's virtual display, input and browser) drives can load `file://`
URLs, and a file URL reads straight from the Cell's disk: it never passes the path rules a
`CellSession` (the terminal session every other file access goes through) enforces. A lease (a
Cell lent to one task) lets its browser read only its own scratch directory, so every layer that
can put a URL in front of the browser asks this module the same question: the Worker's browser tool
before it proposes a navigation, the Capping gate's allowlist rung before it applies one, and each
browser before it loads any file URL at all, a link or a frame included. The rule is lexical and
mirrors how Chromium canonicalises a file URL (backslashes read as separators, percent-escapes
decoded, dot segments collapsed), so a URL cannot look inside scratch here and land outside it
there; anything that cannot be read as an absolute path counts as outside. Only a browser backend
shares the Cell's filesystem, so resolving a symlink is its job, not this module's.

WHY a path's flavour comes from the path, not from this process's OS: the Cell whose disk a URL
reads need not run the OS this check runs on (a Linux Cell's scratch, `/home/bee/scratch`, checked
from a Windows Hive Stand). A URL or root that names a drive (`C:/x`) is compared the Windows way,
case-insensitively; one that does not is compared the POSIX way, case-sensitively; and a URL is
only ever inside a root of its own flavour. This is the same convention
`hivemind.supervision.capping.leave.classify` applies to a Cell's paths.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard`. Called by
    `hivemind.supervision.capping.checks.deterministic.GuiAllowlistCheck`, the Exoskeleton's
    browsers (`hivemind.exoskeleton.browser`) and the Worker's browser tools
    (`hivemind.workers.tools.exoskeleton`). Calls into the standard library only.

Key invariants:
    - Pure: no I/O. A path is compared as written, never resolved against a disk.
    - A URL that is not a file URL never escapes here; other rules (the gate's `net:<host>`
      capability) govern it.
    - A file URL escapes unless it names this machine and its normalised path lies inside one of
      the roots; a relative, remote or unreadable file URL always escapes.
    - Flavour never widens anything: a path with a drive is only inside a root with a drive, a
      path without one only inside a root without one.

See Also:
    - hivemind.cell.session.resolve_scratch_path for the rule a session applies to a path.
    - hivemind.exoskeleton.browser.playwright.guard for the route that applies this rule, then
      resolves symlinks, on every file request the real browser makes.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from collections.abc import Sequence
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

FILE_SCHEME = "file"  # urlsplit lowercases a scheme, so "FILE:" compares equal to this.
# The hosts a file URL may name and still mean this machine: file:///x and file://localhost/x.
LOCAL_FILE_HOSTS = frozenset({"", "localhost"})
# A drive at the start of a file URL's path: /C:/x, or the older /C|/x Chromium still accepts, and
# C:/x itself (file:C:/x, which Chromium canonicalises to file:///C:/x).
_DRIVE = re.compile(r"^/?([A-Za-z])[:|](?=/|$)")

__all__ = [
    "FILE_SCHEME",
    "LOCAL_FILE_HOSTS",
    "file_url_escapes",
    "file_url_path",
    "is_file_url",
    "is_within",
]


def is_file_url(url: str) -> bool:
    """Return whether `url` uses the file scheme, whatever it names.

    Args:
        url: Any URL, as a model or a page wrote it.

    Returns:
        True for a file URL, well-formed or not.
    """
    return urlsplit(url).scheme == FILE_SCHEME


def file_url_path(url: str) -> PurePath | None:
    """Return the absolute path a local file URL names, normalised lexically, in its own flavour.

    Args:
        url: Any URL.

    Returns:
        The path with backslashes read as separators, percent-escapes decoded and dot segments
        collapsed: a PureWindowsPath when it names a drive, a PurePosixPath otherwise. None when
        `url` is not a file URL, names another host, or does not name an absolute path.

    Example:
        >>> file_url_path("file:///srv/scratch/%2E%2E/secret").as_posix()
        '/srv/secret'
        >>> file_url_path("file:///C:/lease/scratch/../secret").as_posix()
        'C:/lease/secret'
    """
    parts = urlsplit(url)
    # A file URL naming a host reaches a share on the network (file://server/x), not this machine.
    if parts.scheme != FILE_SCHEME or (parts.hostname or "") not in LOCAL_FILE_HOSTS:
        return None
    # WHY: Chromium reads a backslash in a file URL's path as a separator on every platform (the
    # URL standard's rule for special schemes), so "\.." must collapse here as it will there; only
    # then are percent-escapes decoded, so an encoded "%5C" stays a name character on POSIX.
    text = unquote(parts.path.replace("\\", "/"))
    if "\x00" in text:
        return None  # No filesystem path contains NUL; a URL that decodes to one is hostile.
    drive = _DRIVE.match(text)
    if drive is not None:
        # A drive letter: a Windows path, where a decoded backslash is a separator too.
        rest = text[drive.end() :] or "/"
        windows = PureWindowsPath(ntpath.normpath(f"{drive.group(1)}:{rest}"))
        return windows if windows.is_absolute() else None
    if not text.startswith("/"):
        return None  # file:relative has no meaning a lease could check; treat it as outside.
    return PurePosixPath(posixpath.normpath(text))


def is_within(path: PurePath, roots: Sequence[PurePath]) -> bool:
    """Return whether `path` is one of `roots` or lies under one, comparing normalised paths.

    Args:
        path: The path to place; expected absolute. A path with a drive is compared the Windows
            way, one without the POSIX way, whatever OS this runs on (module docstring).
        roots: The directories it may be in; expected absolute. Empty allows nothing.

    Returns:
        True when some root of the same flavour equals `path` or is one of its parents, once
        both are normalised.
    """
    candidate = _lexical(path)
    # Normalising the roots too means a root written with "." or a trailing slash still matches;
    # a root of the other flavour can never contain the candidate.
    for root in roots:
        base = _lexical(root)
        if type(base) is type(candidate) and (candidate == base or base in candidate.parents):
            return True
    return False


def file_url_escapes(url: str, roots: Sequence[PurePath]) -> bool:
    """Return whether `url` is a file URL that reaches outside every one of `roots`.

    Args:
        url: Any URL a browser might load.
        roots: The directories a file URL may load from, normally just the lease's scratch.

    Returns:
        False for any URL that is not a file URL; for a file URL, True unless it names this
        machine and its normalised path lies inside one of `roots`.

    Example:
        >>> file_url_escapes("file:///srv/scratch/page.html", [PurePosixPath("/srv/scratch")])
        False
        >>> file_url_escapes("file:///srv/scratch/../secret", [PurePosixPath("/srv/scratch")])
        True
        >>> file_url_escapes("https://example.test/", [])
        False
    """
    if not is_file_url(url):
        return False
    path = file_url_path(url)
    return path is None or not is_within(path, roots)


def _lexical(path: PurePath) -> PurePath:
    """Normalise `path` in the flavour its own text names: Windows with a drive, POSIX without."""
    if path.drive:
        return PureWindowsPath(ntpath.normpath(str(path)))
    return PurePosixPath(posixpath.normpath(path.as_posix()))
