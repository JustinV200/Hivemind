"""Keep a lease's Chromium out of its own DevTools and downloads, over the CDP link it is driven by.

Desktop input lands in the browser window like a person's would, so a key chord or the context
menu's Inspect can open DevTools, whose console runs code with the browser's own privileges and
whose workspace reads and writes local folders (ADR-0058). Chromium's per-profile switch for that
(`devtools.availability`) also refuses remote debugging, which is how the lease drives the
browser, so DevTools is closed instead: `ChromeGuard` opens a browser-level CDP session, asks to
be told about every target, and closes any whose URL is a DevTools page the moment it appears or
navigates there (checked on Edge 154 on Windows, 2026-10-02: a DevTools window opened by
`--auto-open-devtools-for-tabs` arrived as a target and `Target.closeTarget` closed it). It also
denies every download, since no tool reads one and a download chooses its own file name; the
profile already keeps any download inside scratch (`browser.profile`) as a second layer.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Installed by `.page.connect_browser` before the
    first page loads. Calls into Playwright, `browser.hosts` (the block record) and
    `hivemind.common.logging`.

Key invariants:
    - Every DevTools target the browser reports is closed, and each closing is reported once.
    - A target that is already gone when it is closed is not an error.

See Also:
    - docs/adr/0044-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
    - hivemind.exoskeleton.browser.keys.chrome_chord for the chords the tools refuse first.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Browser as CdpBrowser
from playwright.async_api import CDPSession
from playwright.async_api import Error as PlaywrightError

from hivemind.common.logging import get_logger
from hivemind.exoskeleton.browser.hosts import BlockRule, BlockSink, BrowserBlock

# How a DevTools front end's URL starts: Chromium's current scheme and the one it replaced.
DEVTOOLS_SCHEMES = ("devtools://", "chrome-devtools://")

log = get_logger(__name__)

__all__ = ["DEVTOOLS_SCHEMES", "ChromeGuard", "is_devtools"]


def is_devtools(url: str) -> bool:
    """Return whether `url` is a DevTools front end's page.

    Args:
        url: A target's URL, as CDP reports it.

    Returns:
        True for a devtools:// (or the older chrome-devtools://) URL, in any case.
    """
    return url.lower().startswith(DEVTOOLS_SCHEMES)


class ChromeGuard:
    """Close every DevTools window of one browser as it opens, and deny its downloads.

    Owns mutable state (codingrules 8.5): the CDP session `install` opens, and the targets it has
    already closed, so a target reported twice (created, then changed) is reported once.
    """

    def __init__(self, on_blocked: BlockSink | None = None) -> None:
        """Build the guard.

        Args:
            on_blocked: Where each closed DevTools window is reported; None, the log alone.
        """
        self._on_blocked = on_blocked
        self._session: CDPSession | None = None
        self._closed: set[str] = set()

    async def install(self, browser: CdpBrowser) -> None:
        """Watch `browser`'s targets for DevTools and deny its downloads.

        Latency: three round trips to the browser.

        Args:
            browser: Playwright's CDP connection to the lease's browser.

        Raises:
            PlaywrightError: The session could not be opened or a command failed; the caller
                fails the attach.
        """
        # External await: one round trip each, to open the session and send two commands.
        session = await browser.new_browser_cdp_session()
        self._session = session
        session.on("Target.targetCreated", self.seen)
        session.on("Target.targetInfoChanged", self.seen)
        # Every target that already exists is reported at once, so one open now is closed too.
        await session.send("Target.setDiscoverTargets", {"discover": True})
        await session.send("Browser.setDownloadBehavior", {"behavior": "deny"})

    async def seen(self, event: dict[str, Any]) -> None:
        """Close the target `event` describes when it is a DevTools page; never raise.

        Args:
            event: A `Target.targetCreated` or `Target.targetInfoChanged` event's parameters.
        """
        info = event.get("targetInfo") or {}
        target_id, url = info.get("targetId"), info.get("url")
        if not isinstance(target_id, str) or not isinstance(url, str) or not is_devtools(url):
            return
        if target_id in self._closed or self._session is None:
            return
        self._closed.add(target_id)
        try:
            # External await: one round trip to the browser to close the window.
            await self._session.send("Target.closeTarget", {"targetId": target_id})
        except PlaywrightError as error:
            # Closed already, or the browser went away: either way it is no longer open.
            log.debug("exoskeleton.browser_devtools_close_failed", reason=type(error).__name__)
        log.info("exoskeleton.browser_devtools_closed")
        if self._on_blocked is not None:
            # External await: the sink records on the trail.
            await self._on_blocked(BrowserBlock(BlockRule.DEVTOOLS))
