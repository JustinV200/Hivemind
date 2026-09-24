"""Attach Playwright to a lease's Chromium over CDP: the connection a PlaywrightBrowser drives.

A lease's Chromium serves the Chrome DevTools Protocol on a loopback port it chose itself
(`browser.launch`). `attach_cdp` starts Playwright's driver beside the bee, never on the Cell
(ADR-0031), connects it to that port with `connect_over_cdp`, and picks the page every later call
drives: the browser's default context and the page it opened on about:blank, or a new one when it
has none. The four handles travel together as a `CdpConnection`; `PlaywrightTimeouts` says how long
each kind of call on them may take. Either the attach succeeds whole or the driver it started is
stopped again before the error leaves, so a failed attach leaves nothing beside the bee.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Called by `.page.connect_browser`. Calls into
    Playwright, `browser.excerpts` and `hivemind.exoskeleton.errors` only.

Key invariants:
    - `attach_cdp` raises only AttachError, and only after stopping any driver it started.
    - The attach is bounded by `connect_s` plus a margin, whatever Playwright does.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for Playwright over CDP.
    - hivemind.exoskeleton.browser.playwright.page for the PlaywrightBrowser built on this.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import Browser as CdpBrowser
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from hivemind.exoskeleton.browser.excerpts import detail
from hivemind.exoskeleton.browser.playwright.calls import CALL_MARGIN_S
from hivemind.exoskeleton.errors import AttachError

DEFAULT_CONNECT_TIMEOUT_S = 10.0  # Attaching over loopback to a browser that wrote its port.

__all__ = ["DEFAULT_CONNECT_TIMEOUT_S", "CdpConnection", "PlaywrightTimeouts", "attach_cdp"]


@dataclass(frozen=True, slots=True)
class PlaywrightTimeouts:
    """How long each kind of Playwright call may take, in seconds; every value > 0.

    The launcher's settings supply the first two (`browser.launch.ChromiumSettings`), so there is
    one default for each in the Hive.
    """

    navigation_s: float  # A page load, a screenshot, a restore's reload.
    element_s: float  # An element appearing or accepting an action.
    connect_s: float = DEFAULT_CONNECT_TIMEOUT_S  # Attaching over CDP.

    @property
    def limit_s(self) -> float:
        """The asyncio bound on any one call: the longest Playwright timeout plus a margin."""
        return max(self.navigation_s, self.element_s) + CALL_MARGIN_S


@dataclass(frozen=True, slots=True)
class CdpConnection:
    """Playwright's handles on one lease's browser."""

    driver: Playwright  # Playwright's driver process, beside the bee.
    browser: CdpBrowser  # The CDP connection to the lease's Chromium.
    context: BrowserContext  # The browser's default context, where its first page lives.
    page: Page  # The page every Browser call drives.


async def attach_cdp(endpoint: str, timeouts: PlaywrightTimeouts) -> CdpConnection:
    """Start Playwright's driver and attach it to the Chromium serving DevTools at `endpoint`.

    Args:
        endpoint: "http://127.0.0.1:<port>", the port the browser wrote to DevToolsActivePort.
        timeouts: `connect_s` bounds the attach.

    Returns:
        The driver, the CDP connection, the default context and the page to drive.

    Raises:
        AttachError: The driver would not start or the browser could not be attached; a driver
            this call started is already stopped.
    """
    try:
        # Playwright's driver is a local process beside the bee; it starts in well under a second.
        driver = await async_playwright().start()
    except (PlaywrightError, OSError) as error:
        raise AttachError(f"could not start Playwright's driver: {detail(str(error))}") from error
    try:
        # Attaching over loopback takes milliseconds; bounded by connect_s either way.
        async with asyncio.timeout(timeouts.connect_s + CALL_MARGIN_S):
            cdp = await driver.chromium.connect_over_cdp(endpoint, timeout=timeouts.connect_s * 1e3)
            context = cdp.contexts[0] if cdp.contexts else await cdp.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
    except (PlaywrightError, TimeoutError) as error:
        await driver.stop()
        why = detail(str(error)) or "it did not answer in time"
        raise AttachError(f"could not attach to the browser over CDP: {why}") from error
    return CdpConnection(driver=driver, browser=cdp, context=context, page=page)
