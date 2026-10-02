"""Hold a lease's WebSockets to its granted hosts: a route over every ws and wss connection.

A WebSocket is not a request a Playwright route sees, so before this guard a page could open one to
any host. Playwright's WebSocket route (`route_web_socket`) asks about each connection a page or
frame opens before it reaches the network: `SocketGuard` lets it through to the server only when
the lease's `HostScopes` allow its host, and otherwise closes it with a policy-violation code and
reports the host (checked on Edge 154 on Windows, 2026-10-02: a page's and a same-origin frame's
socket to an ungranted host were closed without the host ever seeing a connection).

Playwright hooks sockets from inside each page, so a worker's own socket is not routed; the page
policy the worker's script is answered with (`.requests`) holds that one instead.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright.hosts`. Installed by `.requests.HostGuard`. Calls
    into Playwright, `browser.hosts` and `hivemind.common.logging`.

Key invariants:
    - Every routed socket is either connected to its server or closed, exactly once.
    - Nothing is logged about a socket but its host, and only on refusal.

See Also:
    - docs/adr/0059-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
"""

from __future__ import annotations

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import WebSocketRoute

from hivemind.common.logging import get_logger
from hivemind.exoskeleton.browser.hosts import (
    SOCKET_SCHEMES,
    BlockRule,
    BlockSink,
    BrowserBlock,
    HostScopes,
    request_host,
)

POLICY_VIOLATION = 1008  # The WebSocket close code for "refused by policy" (RFC 6455, 7.4.1).
CLOSE_REASON = "blocked by the lease's net scopes"  # What the page's close event says.

log = get_logger(__name__)

__all__ = ["CLOSE_REASON", "POLICY_VIOLATION", "SocketGuard", "is_socket_url"]


def is_socket_url(url: str) -> bool:
    """Return whether `url` is one the socket route answers: any ws or wss URL.

    Args:
        url: A socket's URL, as Playwright hands it to the route's matcher.

    Returns:
        True for ws and wss; False for every other scheme.
    """
    scheme, separator, _ = url.partition(":")
    return bool(separator) and scheme.lower() in SOCKET_SCHEMES


class SocketGuard:
    """Answer every WebSocket a lease's pages open: connect to a granted host only."""

    def __init__(self, hosts: HostScopes, on_blocked: BlockSink | None = None) -> None:
        """Build the guard for one browser.

        Args:
            hosts: The hosts the lease may reach (`BrowserLaunch.hosts`).
            on_blocked: Where each refusal is reported; None reports it to the log alone.
        """
        self._hosts = hosts
        self._on_blocked = on_blocked

    async def decide(self, socket: WebSocketRoute) -> None:
        """Connect one socket to a granted host, close it otherwise; never raise.

        Args:
            socket: The socket a page opened, held until this answers.
        """
        host = request_host(socket.url)
        try:
            if host is not None and self._hosts.allows(host):
                socket.connect_to_server()  # Messages then flow both ways untouched.
                return
            # External await: one round trip to the page to close the socket it opened.
            await socket.close(code=POLICY_VIOLATION, reason=CLOSE_REASON)
        except PlaywrightError as error:
            # The page closed while deciding: nothing is left to answer.
            log.debug("exoskeleton.browser_socket_route_gone", reason=type(error).__name__)
            return
        log.info("exoskeleton.browser_socket_blocked", host=host)
        if self._on_blocked is not None:
            # External await: the sink records on the trail, after the socket was closed.
            await self._on_blocked(BrowserBlock(BlockRule.NET, host))
