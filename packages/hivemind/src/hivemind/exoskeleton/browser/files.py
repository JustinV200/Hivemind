"""Refuse a file URL outside a browser's file roots, worded the same by every browser.

A lease's browser may load a `file://` URL only from its file roots, normally the lease's scratch
directory (`BrowserLaunch.file_roots`), because a file URL reads the Cell's disk without passing
the path rules a `CellSession` (the terminal session every other file access goes through)
enforces. Whether a URL escapes is `hivemind.guard.file_urls`' question; this module is the one
place a browser turns the answer into its refusal, so the real browser and the fake fail the same
way and the contract suite can hold both to it. The refusal never names the URL: a path on the
Cell can itself be something the operator would not want in a tool result or a log.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Called by `browser.playwright` (before a navigation, and by the route that guards every file
    request) and `browser.fake`. Calls into `hivemind.guard` and `hivemind.exoskeleton.errors`.

Key invariants:
    - A URL that is not a file URL is never refused here.
    - The refusal carries a fixed reason and never the URL or its path.

See Also:
    - hivemind.guard.file_urls for the lexical rule.
    - hivemind.exoskeleton.browser.playwright.guard for the route that also resolves symlinks.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from hivemind.exoskeleton.browser.targets import PERIPHERAL
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.guard import file_url_escapes

# The one reason both browsers give; no URL, since a path can say more than it should.
OUTSIDE_FILE_ROOTS = "a file URL outside the lease's scratch is never loaded"

__all__ = ["OUTSIDE_FILE_ROOTS", "refuse_outside_roots"]


def refuse_outside_roots(url: str, roots: Sequence[Path], operation: str) -> None:
    """Raise when `url` is a file URL outside `roots`; return quietly for anything else.

    Args:
        url: The URL about to be loaded.
        roots: The browser's file roots; empty refuses every file URL.
        operation: What was being done ("navigate", "click", "restore"), for the error.

    Raises:
        PeripheralError: `url` is a file URL that names another host, is not absolute, or lies
            outside every root.
    """
    if file_url_escapes(url, roots):
        raise PeripheralError(PERIPHERAL, operation, OUTSIDE_FILE_ROOTS)
