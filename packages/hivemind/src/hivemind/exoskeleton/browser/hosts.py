"""Decide which hosts a lease's browser may request, and say how anything it blocks is reported.

A lease's browser is granted hosts the way every other tool is: by the bee's `net` capabilities.
Only a navigation the bee proposes passes the Capping gate, though; a link it clicks, a form it
submits, and every image, script and fetch a page makes go straight to the network. So the
browser holds every request to the same rule (ADR-0058): a web request may go to a host only when
the bee holds the `net` scope that host needs (`hivemind.supervision.capping.host_need`), and a
loopback host needs `net:loopback` by name. The one exception is a Cell whose loopback is its own
(a Virtual Cell's container, where nothing else listens), which the composition root says with
`HostScopes.private_loopback`; that is a fact about the Cell's isolation, never its kind. This
module is the pure half: the rule, the host a request names, and the record of what a guard
blocked (a request, or a DevTools window closed as it opened). The Playwright backend's guards
apply it; attach supplies the sink that puts each block on the trail.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Built by `hivemind.exoskeleton.attach` into `BrowserLaunch`; read by
    `browser.playwright.hosts`. Calls into `hivemind.guard` (CapabilitySet) and
    `hivemind.supervision.capping` (the host rule) only.

Key invariants:
    - The default `HostScopes` allows no host at all: a launch that names no grant reaches nothing.
    - A host the `net` grammar cannot hold is refused, never raised.
    - A loopback host is allowed only by `net:loopback` or a private loopback, whatever grant
      covers some other spelling of it.

See Also:
    - docs/adr/0044-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
    - hivemind.exoskeleton.browser.playwright.hosts for the route that applies this rule.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit

from hivemind.guard import CapabilitySet
from hivemind.supervision.capping import host_need, is_loopback_host

WEB_SCHEMES = frozenset({"http", "https"})  # The schemes a request route sees and judges.

__all__ = [
    "WEB_SCHEMES",
    "BlockRule",
    "BlockSink",
    "BrowserBlock",
    "HostScopes",
    "request_host",
]


class BlockRule(Enum):
    """Which of the browser's guards blocked something."""

    FILE = "file"  # A file URL outside the lease's file roots, or hard-linked (ADR-0034, 0058).
    NET = "net"  # A web request to a host no `net` scope the bee holds covers (ADR-0058).
    DEVTOOLS = "devtools"  # A DevTools window, closed as it opened (ADR-0058).


@dataclass(frozen=True, slots=True)
class BrowserBlock:
    """One thing a guard blocked: the rule, and the host for a web request (never a path or URL)."""

    rule: BlockRule
    host: str | None = None  # The host a NET block named; None for every other rule.


# Where a guard reports what it blocked: attach records it on the trail. It is awaited after the
# request is answered, so a slow sink never holds a page's load.
BlockSink = Callable[[BrowserBlock], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HostScopes:
    """The hosts one lease's browser may request: its bee's `net` scopes, plus a private loopback.

    Attributes:
        granted: The bee's capabilities; only its `net` scopes matter here.
        private_loopback: Whether the Cell's loopback is its own (a Virtual Cell's container), so
            a loopback host stays in the lease. False, the default, treats loopback as the
            host's own services, which only `net:loopback` reaches.
    """

    granted: CapabilitySet = field(default_factory=CapabilitySet.empty)
    private_loopback: bool = False

    def allows(self, host: str) -> bool:
        """Return whether a request may go to `host`.

        Args:
            host: The request's host, as `request_host` read it.

        Returns:
            True for a loopback host on a private loopback, or a host whose `net` need the bee
            holds; False otherwise, a host the grammar cannot hold included.
        """
        if self.private_loopback and is_loopback_host(host):
            return True
        needed = host_need(host)
        return needed is not None and self.granted.allows(needed)


def request_host(url: str) -> str | None:
    """Return the host a web request names, or None for any other URL.

    Args:
        url: A request's URL, as the browser reports it.

    Returns:
        The lowercased host of an http or https URL; None for another scheme (file, data, blob,
        about), which this rule does not judge, and for a web URL with no readable host, which
        the route refuses since it cannot show the host is granted.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname  # Lowercased, brackets dropped.
    except ValueError:
        return None  # An unbalanced bracket: no host can be read, so none is granted.
    if parts.scheme.lower() not in WEB_SCHEMES:
        return None
    return host or None
