"""Decide which hosts a lease's browser may request, and say how anything it blocks is reported.

A lease's browser is granted hosts the way every other tool is: by the bee's `net` capabilities.
Only a navigation the bee proposes passes the Capping gate, though; a link it clicks, a form it
submits, and every image, script and fetch a page makes go straight to the network. So the
browser holds every request to the same rule (ADR-0059): a web request may go to a host only when
the bee holds the `net` scope that host needs (`hivemind.supervision.capping.host_need`), and a
loopback host needs `net:loopback` by name. The one exception is a Cell whose loopback is its own
(a Virtual Cell's container, where nothing else listens), which the composition root says with
`HostScopes.private_loopback`; that is a fact about the Cell's isolation, never its kind. This
module is the pure half: the rule, the host a request names, where a redirect leads, the same rule
written as a page's content security policy, and the record of what a guard blocked (a request,
a socket, or a DevTools window closed as it opened). The Playwright backend's guards apply it;
attach supplies the sink that puts each block on the trail.

A route sees only a request's first URL, so the rule is also stated to the browser itself:
`page_policy` is a `default-src` policy naming only hosts the rule allows, which the browser checks
on every request a page or worker makes, each redirect hop included, and on its sockets. It may
name fewer hosts than the rule allows (an IPv6 literal or a network range has no spelling in a
source list), never more.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Built by `hivemind.exoskeleton.attach` into `BrowserLaunch`; read by
    `browser.playwright.hosts`. Calls into `hivemind.guard` (capabilities),
    `hivemind.supervision.capping` (the host rule) and the standard library only.

Key invariants:
    - The default `HostScopes` allows no host at all: a launch that names no grant reaches nothing.
    - A host the `net` grammar cannot hold is refused, never raised.
    - A loopback host is allowed only by `net:loopback` or a private loopback, whatever grant
      covers some other spelling of it.
    - Every host `page_policy` names is one `HostScopes.allows`; it is None only when all are.

See Also:
    - docs/adr/0059-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
    - hivemind.exoskeleton.browser.playwright.hosts for the route that applies this rule.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urljoin, urlsplit

from hivemind.guard import Capability, CapabilityFamily, CapabilitySet
from hivemind.supervision.capping import LOOPBACK_SCOPE, host_need, is_loopback_host

WEB_SCHEMES = frozenset({"http", "https"})  # The schemes a request route sees and judges.
SOCKET_SCHEMES = frozenset({"ws", "wss"})  # The schemes a WebSocket route sees and judges.
POLICY_SCHEMES = ("http", "https", "ws", "wss")  # Each granted host's sources, in this order.
# The statuses a browser follows to their Location; it shows a 300 or a 304 as it is.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# How each loopback host a source list can spell is written: the IPv4 address pages use and the
# localhost names (RFC 6761). `[::1]` has no spelling there, so pages cannot load from it.
LOOPBACK_SOURCES = ("127.0.0.1", "localhost", "*.localhost")
# What every page may still load that names no host: its own inline and evaluated code (a policy
# of ours must not break a page's scripts), and data and blob URLs, which never leave the browser.
LOCAL_SOURCES = "data: blob: 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'"
_SUBDOMAINS = "*."  # A `net` scope that starts so covers every strict subdomain of the rest.

__all__ = [
    "LOCAL_SOURCES",
    "LOOPBACK_SOURCES",
    "POLICY_SCHEMES",
    "REDIRECT_STATUSES",
    "SOCKET_SCHEMES",
    "WEB_SCHEMES",
    "BlockRule",
    "BlockSink",
    "BrowserBlock",
    "HostScopes",
    "page_policy",
    "redirect_target",
    "request_host",
]


class BlockRule(Enum):
    """Which of the browser's guards blocked something."""

    FILE = "file"  # A file URL outside the lease's file roots, or hard-linked (ADR-0034, 0058).
    NET = "net"  # A request, redirect or socket to a host no `net` scope covers (ADR-0059).
    DEVTOOLS = "devtools"  # A DevTools window, closed as it opened (ADR-0059).


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
    """Return the host a web request or WebSocket names, or None for any other URL.

    Args:
        url: A request's URL, as the browser reports it.

    Returns:
        The lowercased host of an http, https, ws or wss URL; None for another scheme (file,
        data, blob, about), which this rule does not judge, and for such a URL with no readable
        host, which the guards refuse since they cannot show the host is granted.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname  # Lowercased, brackets dropped.
    except ValueError:
        return None  # An unbalanced bracket: no host can be read, so none is granted.
    if parts.scheme.lower() not in WEB_SCHEMES | SOCKET_SCHEMES:
        return None
    return host or None


def redirect_target(url: str, status: int, location: str | None) -> str | None:
    """Return where a response sends the browser next, or None when it is not a redirect.

    Args:
        url: The URL the response answered.
        status: Its status code.
        location: Its Location header, if any.

    Returns:
        `location` resolved against `url` for a status the browser follows; None for any other
        status, or a redirect with nowhere to go.

    Example:
        >>> redirect_target("https://a.example/x/y", 302, "../z")
        'https://a.example/z'
    """
    if status not in REDIRECT_STATUSES or not location:
        return None
    return urljoin(url, location)


def page_policy(hosts: HostScopes) -> str | None:
    """Write the lease's host rule as the content security policy every page and worker gets.

    Args:
        hosts: The hosts the lease may reach.

    Returns:
        A `default-src` policy naming, on any port, each granted host a source list can spell
        (and loopback where the rule allows it), then LOCAL_SOURCES; None when every host is
        allowed, since a policy would then refuse nothing.

    Example:
        >>> page_policy(HostScopes(CapabilitySet.parse("net:a.example"))).split()[:3]
        ['default-src', 'http://a.example:*', 'https://a.example:*']
    """
    if hosts.granted.allows(Capability.parse("net:*")):
        return None
    # `net:loopback` is a word, not a host: it is spelled out as LOOPBACK_SOURCES below.
    scopes = (cap.scope for cap in hosts.granted if cap.family is CapabilityFamily.NET)
    names = {_source(scope, hosts) for scope in scopes if scope != LOOPBACK_SCOPE}
    if hosts.allows(LOOPBACK_SOURCES[0]):
        names.update(LOOPBACK_SOURCES)
    # Each scheme spelled out: Chromium matches a source with no scheme to http and https only, so
    # it would refuse a granted host's WebSocket (checked on Edge 154, 2026-10-02).
    sources = [
        f"{scheme}://{name}:*"
        for name in sorted(name for name in names if name is not None)
        for scheme in POLICY_SCHEMES
    ]
    return " ".join(["default-src", *sources, LOCAL_SOURCES])


def _source(scope: str, hosts: HostScopes) -> str | None:
    """Spell one held `net` scope as a source host; None when it has no spelling."""
    # Every candidate is put to the rule itself, so a grant naming a loopback host some other way
    # (which the rule refuses) never reaches the policy.
    if scope.startswith(_SUBDOMAINS):
        return scope if hosts.allows(f"a{scope[1:]}") else None
    try:
        network = ipaddress.ip_network(scope)
    except ValueError:
        return scope if hosts.allows(scope) else None  # A DNS name.
    # A single IPv4 address only: a range, and any IPv6 literal, have no spelling in a source list.
    if network.version == 4 and network.num_addresses == 1:
        address = str(network.network_address)
        return address if hosts.allows(address) else None
    return None
