"""Define the Exoskeleton's browser attachment: the browser fast path, and how it is launched.

A task that needs a web page gets a real browser on its Cell, driven through `Browser` (navigate,
act on an element by accessible role and name, read the page back as structure) rather than through
raw pixels and input, so a model without vision can still do browser work (roadmap step 6.11,
ADR-0031). `BrowserLauncher` starts that browser through the Cell's session for one lease.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Used by `hivemind.exoskeleton.attach`, the Capping gate's GUI surface and the `browser_*`
    tools. Calls into `hivemind.cell`, `hivemind.exoskeleton.frames` and `.scratch`.

Key invariants:
    - Nothing here imports Playwright; only `browser.playwright` does (an import-linter contract).

See Also:
    - hivemind.exoskeleton.browser.base for the protocols.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the fast path.

Public API:
    - Browser, BrowserCheckpoint: drive a page and take its undo point (base).
    - BrowserLauncher, BrowserLaunch, LaunchedBrowser: start a lease's browser (base).
"""

from hivemind.exoskeleton.browser.base import (
    Browser,
    BrowserCheckpoint,
    BrowserLaunch,
    BrowserLauncher,
    LaunchedBrowser,
)

__all__ = ["Browser", "BrowserCheckpoint", "BrowserLaunch", "BrowserLauncher", "LaunchedBrowser"]
