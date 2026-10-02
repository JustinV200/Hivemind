"""Turn WebRTC off in a lease's browser: no page can open a peer connection to any host.

WebRTC reaches the network outside every route: its STUN and TURN servers and its peers are named
by the page, and its traffic is neither a request nor a WebSocket. The profile already stops its
UDP and the local addresses it would show (`browser.profile` sets `webrtc.ip_handling_policy`),
but that setting still lets it reach a TURN server over TCP (checked on Edge 154 on Windows,
2026-10-02: with the setting a page gathered no candidates, yet opened a TCP connection to a TURN
server on an ungranted host). So an init script removes the peer connection constructors from
every page and frame before the page's own code runs; nothing else in the browser starts WebRTC,
and workers never had it. The fast path's work (pages, forms, logins) needs no peer connection,
and a host grant does not restore it: the servers and peers a page names are not hosts it was
granted.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright.hosts`. Installed by `.requests.HostGuard`. Calls
    into Playwright only.

Key invariants:
    - Installed on the context before the first page loads, so it runs in every document after.

See Also:
    - docs/adr/0059-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
"""

from __future__ import annotations

import json

from playwright.async_api import BrowserContext

# The globals a page builds a peer connection from, with Chromium's old prefixed alias. A frame
# the page adds runs the script too, so a fresh frame's window does not bring them back.
PEER_GLOBALS = (
    "RTCPeerConnection",
    "webkitRTCPeerConnection",
    "RTCSessionDescription",
    "RTCIceCandidate",
    "RTCDataChannel",
)
PEER_CONNECTIONS_OFF = (
    f"for (const name of {json.dumps(list(PEER_GLOBALS))}) "
    "{ try { delete globalThis[name]; } catch (error) {} }"
)

__all__ = ["PEER_CONNECTIONS_OFF", "PEER_GLOBALS", "block_peer_connections"]


async def block_peer_connections(context: BrowserContext) -> None:
    """Remove WebRTC's constructors from every page of `context` before its code runs.

    Latency: one round trip to the browser.

    Args:
        context: The lease's browser context.

    Raises:
        PlaywrightError: The script could not be added; the caller fails the attach.
    """
    # External await: one round trip to the browser to register the init script.
    await context.add_init_script(script=PEER_CONNECTIONS_OFF)
