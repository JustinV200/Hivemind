"""Keep a lease's Chromium on its granted hosts: a route that answers every web request.

Only a navigation the bee proposes passes the Capping gate's `net:<host>` check. A link it clicks
is tiered by the page it starts on, and a page's own scripts, images, frames and fetches go
wherever the page says. `HostGuard` is a Playwright route over every http and https request of the
lease's browser context, installed before the first page loads, like `FileGuard` beside it. A
request to a host the lease's `HostScopes` do not allow is aborted as blocked by the client and
reported, so attach can put it on the trail (ADR-0059).

Playwright asks a route only about a request's first URL: a redirect is followed without asking
again, even after the route answered it itself (checked on Edge 154, 2026-10-02: a fulfilled 302
to a granted host's own redirect to another host reached that host). So the guard answers a
navigation and a script by fetching it itself, one hop only (`route.fetch` with no redirects):

- A redirect to a host the rule refuses is aborted and reported, before the browser sees it.
- A navigation's redirect to an allowed host is answered with a page that navigates there afresh
  (`.answers.reentry_page`), so that hop comes back through this route like the first.
- Anything else is answered with what was fetched, plus the lease's page policy
  (`browser.hosts.page_policy`). With it the browser itself checks every request the page or
  worker makes, each redirect hop and WebSocket included, against the same hosts.

Every other request (an image, a fetch, a stream) is continued in the browser, where it streams
as before; the page's policy holds its redirects. `.sockets` judges WebSockets and `.peers` turns
WebRTC off, both installed with this route.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright.hosts`. Installed by `..page.connect_browser`. Calls
    into Playwright, `browser.hosts`, `.answers`, `.sockets`, `.peers`, `..guard` (the blocked
    error) and `hivemind.common.logging`.

Key invariants:
    - Every web request is answered exactly once; one whose host cannot be read is aborted.
    - No redirect a fetched request answers with is followed to a host the rule refuses.
    - A refusal is reported after the request is answered, so reporting never holds a page.
    - Nothing is logged about a request but its host, and only on refusal.

See Also:
    - hivemind.exoskeleton.browser.hosts for the rule and the page policy.
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
    page_policy,
    redirect_target,
    request_host,
)
from hivemind.exoskeleton.browser.playwright.guard import BLOCKED
from hivemind.exoskeleton.browser.playwright.hosts.answers import (
    REENTRY_HEADERS,
    reentry_page,
    with_policy,
)
from hivemind.exoskeleton.browser.playwright.hosts.peers import block_peer_connections
from hivemind.exoskeleton.browser.playwright.hosts.sockets import SocketGuard, is_socket_url

# The requests the guard fetches itself: a navigation, whose redirect the page policy cannot see
# (it holds a page's requests, not where the page itself goes), and a script, since a worker's
# script carries the policy the worker is held to. Everything else streams in the browser.
FETCHED_TYPES = frozenset({"document", "script"})
DEFAULT_FETCH_TIMEOUT_S = 30.0  # One page or script; the launcher passes its navigation bound.
FAILED = "failed"  # Chromium's generic network error: what the browser shows for a lost fetch.
# A redirect that keeps the method and body. A navigation re-entered as a fresh GET would drop
# the body, so a non-GET one is refused instead of silently changed.
METHOD_KEEPING = frozenset({307, 308})
BODYLESS_METHODS = frozenset({"GET", "HEAD"})

log = get_logger(__name__)

__all__ = ["DEFAULT_FETCH_TIMEOUT_S", "FETCHED_TYPES", "HostGuard", "is_web_request"]


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
    """Answer every web request and WebSocket a lease's browser makes: granted hosts only."""

    def __init__(
        self,
        hosts: HostScopes,
        on_blocked: BlockSink | None = None,
        fetch_timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
    ) -> None:
        """Build the guard for one browser.

        Args:
            hosts: The hosts the lease may reach (`BrowserLaunch.hosts`).
            on_blocked: Where each refusal is reported; None reports it to the log alone.
            fetch_timeout_s: How long fetching one navigation or script may take; > 0.
        """
        self._hosts = hosts
        self._on_blocked = on_blocked
        self._fetch_ms = fetch_timeout_s * 1e3
        self._policy = page_policy(hosts)
        self._sockets = SocketGuard(hosts, on_blocked)

    async def install(self, context: BrowserContext) -> None:
        """Route every web request and WebSocket of `context` through this guard; WebRTC off.

        Latency: three round trips to the browser.

        Args:
            context: The browser context every page of the lease's browser lives in.

        Raises:
            PlaywrightError: A route could not be installed; the caller fails the attach.
        """
        # External await: one round trip to the browser for each route and the init script.
        await context.route(is_web_request, self.decide)
        await context.route_web_socket(is_socket_url, self._sockets.decide)
        await block_peer_connections(context)

    def allows(self, url: str) -> bool:
        """Return whether a request to `url` may go ahead.

        Args:
            url: A web request's URL, or where a redirect leads.

        Returns:
            True only when it is a web URL, its host can be read and the lease's hosts allow it.
        """
        host = request_host(url)
        return host is not None and self._hosts.allows(host)

    async def decide(self, route: Route) -> None:
        """Abort one web request to a refused host, else continue or fetch it; never raise.

        Args:
            route: The paused request Chromium asked about.
        """
        url = route.request.url
        try:
            # External await: one round trip to the browser, or a fetch and one, to answer.
            if not self.allows(url):
                await route.abort(BLOCKED)
                block: BrowserBlock | None = BrowserBlock(BlockRule.NET, request_host(url))
            elif route.request.resource_type not in FETCHED_TYPES:
                await route.continue_()
                return
            else:
                block = await self._fetched(route)
        except PlaywrightError as error:
            # The page closed or the browser went away while deciding: nothing is left to answer.
            log.debug("exoskeleton.browser_host_route_gone", reason=type(error).__name__)
            return
        if block is not None:
            await self._report(block)

    async def _fetched(self, route: Route) -> BrowserBlock | None:
        """Fetch one hop of a navigation or script and answer with it; the block, if any."""
        request = route.request
        try:
            response = await route.fetch(max_redirects=0, timeout=self._fetch_ms)
        except PlaywrightError as error:
            # The host did not answer in time or at all: the browser shows its own failure page.
            log.debug("exoskeleton.browser_host_fetch_failed", reason=type(error).__name__)
            await route.abort(FAILED)
            return None
        target = redirect_target(request.url, response.status, response.headers.get("location"))
        if target is None:
            # Not a redirect: what the page or worker runs carries the policy for all it asks next.
            await route.fulfill(
                response=response, headers=with_policy(response.headers, self._policy)
            )
            return None
        if not self.allows(target):
            # Refused before the browser ever sees the redirect; the host reported is the target's.
            await route.abort(BLOCKED)
            return BrowserBlock(BlockRule.NET, request_host(target))
        if not request.is_navigation_request():
            # A script's next hop is held by the policy of the page that asked for it.
            await route.fulfill(response=response)
            return None
        if response.status in METHOD_KEEPING and request.method not in BODYLESS_METHODS:
            log.info("exoskeleton.browser_redirect_refused", status=response.status)
            await route.abort(FAILED)
            return None
        # The frame navigates to the target itself, so that hop comes back through this route.
        await route.fulfill(status=200, headers=REENTRY_HEADERS, body=reentry_page(target))
        return None

    async def _report(self, block: BrowserBlock) -> None:
        """Log one refusal by host and hand it to the sink."""
        log.info("exoskeleton.browser_host_blocked", host=block.host)
        if self._on_blocked is not None:
            # External await: the sink records on the trail, after the request was answered.
            await self._on_blocked(block)
