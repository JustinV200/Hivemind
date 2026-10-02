"""Keep a lease's Chromium on its granted hosts: its requests, redirects, sockets and peers.

The bee's `net` scopes say which hosts it may reach (`hivemind.exoskeleton.browser.hosts`), and a
lease's browser is held to them however a page tries to leave (ADR-0059). The package splits that
by what a page can open: `requests` routes every web request and fetches navigations and scripts
itself so no redirect is followed unchecked, `answers` builds the page policy header and the page
that re-enters a redirect as a fresh navigation, `sockets` routes every WebSocket, and `peers`
turns WebRTC off. `HostGuard.install` puts all of them on the context together.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Installed by `..page.connect_browser`. Calls into
    Playwright, `browser.hosts` and `hivemind.common.logging`.

Key invariants:
    - Installed before the first page loads, so no page of the lease ever runs unguarded.

See Also:
    - docs/adr/0059-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
    - hivemind.exoskeleton.browser.playwright.guard for the file route beside it.

Public API:
    - HostGuard: routes every web request and WebSocket, and turns WebRTC off (requests).
    - is_web_request: the URLs the request route answers (requests).
    - SocketGuard: the WebSocket route on its own (sockets).
    - is_socket_url: the URLs the socket route answers (sockets).
    - reentry_page, with_policy: how a fetched request is answered (answers).
    - PEER_CONNECTIONS_OFF: the init script that removes WebRTC (peers).
"""

from hivemind.exoskeleton.browser.playwright.hosts.answers import reentry_page, with_policy
from hivemind.exoskeleton.browser.playwright.hosts.peers import PEER_CONNECTIONS_OFF
from hivemind.exoskeleton.browser.playwright.hosts.requests import HostGuard, is_web_request
from hivemind.exoskeleton.browser.playwright.hosts.sockets import SocketGuard, is_socket_url

__all__ = [
    "PEER_CONNECTIONS_OFF",
    "HostGuard",
    "SocketGuard",
    "is_socket_url",
    "is_web_request",
    "reentry_page",
    "with_policy",
]
