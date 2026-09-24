"""Drive the browser fast path with Playwright over CDP: the one package that imports Playwright.

A lease's Chromium (started by `hivemind.exoskeleton.browser.launch`) is driven through
Playwright attached over the Chrome DevTools Protocol on loopback (ADR-0031). This package is the
only place in the Hive that imports Playwright, an optional dependency (the `hivemind[browser]`
extra) that an import-linter contract confines here; the launcher imports it lazily, at launch, so
a Hive without the extra still imports everything else. Its modules split the backend by
responsibility: `calls` (every Playwright call bounded and its errors mapped), `connect` (attaching
over CDP), `storage` (what a checkpoint covers) and `page` (the Browser itself).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser`. Imported lazily by `browser.launch.ChromiumLauncher`; nothing
    else imports it. Calls into Playwright and the browser package's shared modules.

Key invariants:
    - Importing this package needs Playwright installed; importing `hivemind.exoskeleton.browser`
      never does.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why Playwright over CDP.
    - hivemind.exoskeleton.browser.base for the Browser contract.

Public API:
    - connect_browser: attach to a lease's Chromium and return a Browser on its page (page).
    - PlaywrightBrowser: the Browser over one Chromium page (page).
    - PlaywrightTimeouts: how long loads, element waits and the attach may take (connect).
    - redact_field_values: keep password values out of an aria snapshot (page).
"""

from hivemind.exoskeleton.browser.playwright.connect import PlaywrightTimeouts
from hivemind.exoskeleton.browser.playwright.page import (
    PlaywrightBrowser,
    connect_browser,
    redact_field_values,
)

__all__ = [
    "PlaywrightBrowser",
    "PlaywrightTimeouts",
    "connect_browser",
    "redact_field_values",
]
