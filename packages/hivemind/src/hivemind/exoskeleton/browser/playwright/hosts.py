"""Keep a lease's Chromium on its granted hosts: a route that answers every web request.

Only a navigation the bee proposes passes the Capping gate's `net:<host>` check. A link it clicks
is tiered by the page it starts on, and a page's own scripts, images, frames and fetches go
wherever the page says. `HostGuard` is a Playwright route over every http and https request of
the lease's browser context, installed before the first page loads, like `FileGuard` beside it:
it continues a request only when the lease's `HostScopes` allow its host (the bee's `net` scopes,
`net:loopback` for a loopback host unless the Cell's loopback is private) and aborts it as blocked
by the client otherwise, then reports the refusal so attach can put it on the trail (ADR-0058).

Playwright asks a route only about a request's first URL: a redirect a granted server answers
with is followed without asking again. WebSockets, WebRTC and a worker's own connections are not
requests a route sees either. On a Virtual Cell the container's egress policy still bounds all of
them; on a Real Cell this guard is what bounds a page, and those are its known limits (ADR-0058).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Installed by `.page.connect_browser`. Calls into
    Playwright, `browser.hosts`, `.guard` (the blocked error) and `hivemind.common.logging`.

Key invariants:
    - Every web request is answered exactly once; one whose host cannot be read is aborted.
    - A refusal is reported after the request is answered, so reporting never holds a page.
    - Nothing is logged about a request but its host, and only on refusal.

See Also:
    - hivemind.exoskeleton.browser.hosts for the rule.
    - hivemind.exoskeleton.browser.playwright.guard for the file route this mirrors.
"""

from __future__ import annotations

from playwright.async_api import BrowserContext, Route
from playwright.async_api import Error as PlaywrightError

from hivemind.common.logging import get_logger
from hivemind.exoskeleton.browser.hosts import (
    WEB_SCHEMES,
    BlockRule,
    BlockSink,
    BrowserBlock,
    HostScopes,
    request_host,
)
from hivemind.exoskeleton.browser.playwright.guard import BLOCKED

log = get_logger(__name__)

__all__ = ["HostGuard", "is_web_request"]


def is_web_request(url: str) -> bool:
    """Return whether `url` is one the host route answers: any http or https URL.

    Args:
        url: A request's URL, as Playwright hands it to a route's matcher.

    Returns:
        True for http and https, however the host is spelled; False for every other scheme.
    """
    scheme, separator, _ = url.partition(":")
    return bool(separator) and scheme.lower() in WEB_SCHEMES


class HostGuard:
    """Answer every web request a lease's browser makes: continue to a granted host only."""

    def __init__(self, hosts: HostScopes, on_blocked: BlockSink | None = None) -> None:
        """Build the guard for one browser.

        Args:
            hosts: The hosts the lease may reach (`BrowserLaunch.hosts`).
            on_blocked: Where each refusal is reported; None reports it to the log alone.
        """
        self._hosts = hosts
        self._on_blocked = on_blocked

    async def install(self, context: BrowserContext) -> None:
        """Route every web request of `context` through this guard.

        Latency: one round trip to the browser.

        Args:
            context: The browser context every page of the lease's browser lives in.

        Raises:
            PlaywrightError: The route could not be installed; the caller fails the attach.
        """
        # External await: one round trip to the browser to start routing web requests.
        await context.route(is_web_request, self.decide)

    def allows(self, url: str) -> bool:
        """Return whether a request to `url` may go ahead.

        Args:
            url: A web request's URL.

        Returns:
            True only when its host can be read and the lease's hosts allow it.
        """
        host = request_host(url)
        return host is not None and self._hosts.allows(host)

    async def decide(self, route: Route) -> None:
        """Continue one web request to a granted host, abort it otherwise; never raise.

        Args:
            route: The paused request Chromium asked about.
        """
        url = route.request.url
        allowed = self.allows(url)
        try:
            # External await: one round trip to the browser to release or refuse the request.
            if allowed:
                await route.continue_()
                return
            await route.abort(BLOCKED)
        except PlaywrightError as error:
            # The page closed or the browser went away while deciding: nothing is left to answer.
            log.debug("exoskeleton.browser_host_route_gone", reason=type(error).__name__)
            return
        host = request_host(url)
        log.info("exoskeleton.browser_host_blocked", host=host)
        if self._on_blocked is not None:
            # External await: the sink records on the trail, after the request was answered.
            await self._on_blocked(BrowserBlock(BlockRule.NET, host))
