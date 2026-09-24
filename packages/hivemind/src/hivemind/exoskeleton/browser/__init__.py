"""Define the Exoskeleton's browser attachment: the browser fast path, and how it is launched.

A task that needs a web page gets a real browser on its Cell, driven through `Browser` (navigate,
act on an element by accessible role and name, read the page back as structure) rather than through
raw pixels and input, so a model without vision can still do browser work (roadmap step 6.11,
ADR-0031). `BrowserLauncher` starts that browser through the Cell's session for one lease:
`ChromiumLauncher` finds a Chromium-family browser on the Cell (`ChromiumLocator`), starts it with
its files in scratch and connects Playwright to it over CDP; the fakes serve a `FakeSite` instead.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Used by `hivemind.exoskeleton.attach`, the Capping gate's GUI surface and the `browser_*`
    tools. Calls into `hivemind.cell`, `hivemind.exoskeleton.frames`, `.geometry` and `.scratch`.

Key invariants:
    - Nothing imported here imports Playwright; only the `browser.playwright` package does (an
      import-linter contract), and only `ChromiumLauncher.launch` imports that, lazily.

See Also:
    - hivemind.exoskeleton.browser.base for the protocols.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the fast path.
    - hivemind.exoskeleton.browser.playwright for the Playwright backend (needs the extra).

Public API:
    - Browser, BrowserCheckpoint: drive a page and take its undo point (base).
    - BrowserLauncher, BrowserLaunch, LaunchedBrowser: start a lease's browser (base).
    - ChromiumLauncher, ChromiumSettings, CdpConnector: the real launcher and its settings
      (launch).
    - ChromiumLocator, ChromiumExecutable, HostPlatform: find a browser on a Cell (locate).
    - MAX_SNAPSHOT_CHARS, MAX_PAGE_TEXT_CHARS, MAX_ELEMENT_TEXT_CHARS: the caps every read obeys
      (excerpts).
    - FakeBrowser, FakeBrowserLauncher, FakeSite, login_site: the in-memory browser, its launcher,
      a site as data and the fixture login site (fake).
"""

from hivemind.exoskeleton.browser.base import (
    Browser,
    BrowserCheckpoint,
    BrowserLaunch,
    BrowserLauncher,
    LaunchedBrowser,
)
from hivemind.exoskeleton.browser.excerpts import (
    MAX_ELEMENT_TEXT_CHARS,
    MAX_PAGE_TEXT_CHARS,
    MAX_SNAPSHOT_CHARS,
)
from hivemind.exoskeleton.browser.fake import (
    FakeBrowser,
    FakeBrowserLauncher,
    FakeSite,
    login_site,
)
from hivemind.exoskeleton.browser.launch import CdpConnector, ChromiumLauncher, ChromiumSettings
from hivemind.exoskeleton.browser.locate import ChromiumExecutable, ChromiumLocator, HostPlatform

__all__ = [
    "MAX_ELEMENT_TEXT_CHARS",
    "MAX_PAGE_TEXT_CHARS",
    "MAX_SNAPSHOT_CHARS",
    "Browser",
    "BrowserCheckpoint",
    "BrowserLaunch",
    "BrowserLauncher",
    "CdpConnector",
    "ChromiumExecutable",
    "ChromiumLauncher",
    "ChromiumLocator",
    "ChromiumSettings",
    "FakeBrowser",
    "FakeBrowserLauncher",
    "FakeSite",
    "HostPlatform",
    "LaunchedBrowser",
    "login_site",
]
