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
there; anything that cannot be read as an absolute path on this machine counts as outside. Only a
browser backend shares the Cell's filesystem, so resolving a symlink is its job, not this module's.

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

See Also:
    - hivemind.cell.session.resolve_scratch_path for the rule a session applies to a path.
    - hivemind.exoskeleton.browser.playwright.guard for the route that applies this rule, then
      resolves symlinks, on every file request the real browser makes.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

FILE_SCHEME = "file"  # urlsplit lowercases a scheme, so "FILE:" compares equal to this.
# The hosts a file URL may name and still mean this machine: file:///x and file://localhost/x.
LOCAL_FILE_HOSTS = frozenset({"", "localhost"})

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


def file_url_path(url: str) -> Path | None:
    """Return the absolute path on this machine a file URL names, normalised lexically.

    Args:
        url: Any URL.

    Returns:
        The path with backslashes read as separators, percent-escapes decoded and dot segments
        collapsed; None when `url` is not a file URL, names another host, or does not name an
        absolute path.

    Example:
        >>> file_url_path("file:///srv/scratch/%2E%2E/secret").as_posix()
        '/srv/secret'
    """
    parts = urlsplit(url)
    # A file URL naming a host reaches a share on the network (file://server/x), not this machine.
    if parts.scheme != FILE_SCHEME or (parts.hostname or "") not in LOCAL_FILE_HOSTS:
        return None
    # WHY: Chromium reads a backslash in a file URL's path as a separator on every platform (the
    # URL standard's rule for special schemes), so "\.." must collapse here as it will there.
    # url2pathname then decodes percent-escapes, and on Windows maps /C:/x onto C:\x.
    local = url2pathname(parts.path.replace("\\", "/"))
    if "\x00" in local:
        return None  # No filesystem path contains NUL; a URL that decodes to one is hostile.
    path = Path(local)
    if not path.is_absolute():
        return None  # file:relative has no meaning a lease could check; treat it as outside.
    return Path(os.path.normpath(path))


def is_within(path: Path, roots: Sequence[Path]) -> bool:
    """Return whether `path` is one of `roots` or lies under one, comparing normalised paths.

    Args:
        path: The path to place; expected absolute.
        roots: The directories it may be in; expected absolute. Empty allows nothing.

    Returns:
        True when some root equals `path` or is one of its parents, once both are normalised.
    """
    candidate = Path(os.path.normpath(path))
    # Normalising the roots too means a root written with "." or a trailing slash still matches.
    for root in roots:
        base = Path(os.path.normpath(root))
        if candidate == base or base in candidate.parents:
            return True
    return False


def file_url_escapes(url: str, roots: Sequence[Path]) -> bool:
    """Return whether `url` is a file URL that reaches outside every one of `roots`.

    Args:
        url: Any URL a browser might load.
        roots: The directories a file URL may load from, normally just the lease's scratch.

    Returns:
        False for any URL that is not a file URL; for a file URL, True unless it names this
        machine and its normalised path lies inside one of `roots`.

    Example:
        >>> file_url_escapes("file:///srv/scratch/page.html", [Path("/srv/scratch")])
        False
        >>> file_url_escapes("file:///srv/scratch/../secret", [Path("/srv/scratch")])
        True
        >>> file_url_escapes("https://example.test/", [])
        False
    """
    if not is_file_url(url):
        return False
    path = file_url_path(url)
    return path is None or not is_within(path, roots)
