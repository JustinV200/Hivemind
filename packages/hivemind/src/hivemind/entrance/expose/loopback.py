"""Decide whether the loopback listener may answer a request: a loopback Host, and no proxy.

The loopback listener carries every route that admits a device or widens its reach (approve,
deny, unlock, capability widening, reopening; ADR-0041), and it is safe only because nothing but
the Hive Stand itself can reach it. Two things could break that without anyone binding a routable
address. A proxy on the Hive Stand (``tailscale serve``, a reverse proxy, a tunnel client, a
debugging proxy) could relay remote requests to it; the ones in common use announce themselves
with a forwarding header (``Forwarded``, ``X-Forwarded-*``, ``X-Real-IP``, ``Via``, which every
HTTP proxy must add, and Tailscale's ``Tailscale-*`` identity headers), so any of those refuses the
request. And a web page anywhere could aim the operator's own browser at it by DNS rebinding: its
name comes to resolve to 127.0.0.1, but the browser still sends the page's name as ``Host``, so
only a loopback ``Host`` is answered: ``localhost``, any ``127.x.y.z`` or ``[::1]``, with no port or
the listener's own. The check is pure; the app step calls it from middleware before any route
runs, and a refusal is a plain 403 that says nothing about which part failed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Called by the
    loopback listener's middleware for every request. Imports the standard library only.

Key invariants:
    - A request without a ``Host`` header is refused; so is one with any forwarding header,
      whatever its value, and whatever its name's letter case.
    - Only the spellings named above are loopback: ``127.1``, ``0x7f.0.0.1``, ``localhost.``, a
      zoned ``[::1%lo]`` and every name under ``localhost`` are refused, since nothing legitimate
      sends them and each is a way to confuse a parser.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "It answers only
      requests whose Host is a loopback name or address".
    - .claude/codingrules.md section 8.15 ("the loopback listener refuses a non-loopback Host and
      any proxy-forwarding header").
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

_LOCALHOST = "localhost"  # The one loopback name answered (RFC 6761), in any letter case.
_IPV4_LOOPBACK = ipaddress.IPv4Network("127.0.0.0/8")  # Every 127.x.y.z is the host itself.
_IPV6_LOOPBACK = ipaddress.IPv6Address("::1")  # IPv6 has exactly one loopback address.
_PORT = re.compile(r"[0-9]{1,5}")  # ASCII digits only; str.isdigit would admit other scripts.
# Header names (lowercase) whose presence alone means a proxy relayed the request; Via is the one
# RFC 9110 obliges every HTTP proxy to add, the rest are the de facto forwarding headers.
_PROXY_HEADERS = frozenset({"forwarded", "x-real-ip", "via"})
_PROXY_HEADER_PREFIXES = ("x-forwarded-", "tailscale-")  # Families: X-Forwarded-For/Host/...

__all__ = ["loopback_request_allowed"]


def loopback_request_allowed(
    host_header: str | None, header_names: Iterable[str], bind_port: int
) -> bool:
    """Return whether the loopback listener may answer a request with these headers.

    Args:
        host_header: The request's ``Host`` value exactly as received, or None when absent.
        header_names: The name of every header on the request, in any letter case.
        bind_port: The port the loopback listener actually listens on (after an OS-chosen port
            0 is resolved); a ``Host`` naming any other port is refused.

    Returns:
        True only when no forwarding header is present and ``Host`` is ``localhost``, a
        ``127.x.y.z`` address or ``[::1]``, bare or with ``:<bind_port>``.

    Example:
        ``loopback_request_allowed("localhost:8710", ["host", "accept"], 8710)`` is True;
        ``loopback_request_allowed("hive.example:8710", ["host"], 8710)`` is False (rebinding),
        and so is ``loopback_request_allowed("127.0.0.1", ["host", "x-forwarded-for"], 8710)``.
    """
    # Any relay at all is refused before the Host is even read: nothing may front this listener.
    if any(_is_proxy_header(name) for name in header_names):
        return False
    if host_header is None:
        return False
    split = _split_host(host_header)
    if split is None:
        return False
    host, port, bracketed = split
    # A port, when present, must be this listener's own; a rebinding page often names another.
    if port is not None and port != bind_port:
        return False
    return _is_loopback_host(host, bracketed=bracketed)


def _is_proxy_header(name: str) -> bool:
    """Return whether a header name announces a proxy or a forwarded request."""
    lowered = name.lower()
    return lowered in _PROXY_HEADERS or lowered.startswith(_PROXY_HEADER_PREFIXES)


def _split_host(value: str) -> tuple[str, int | None, bool] | None:
    """Split a Host value into host, optional port and whether the host was bracketed."""
    bracketed = value.startswith("[")
    if bracketed:
        # An IPv6 literal keeps its colons inside the brackets; the port follows the "]".
        host, closing, rest = value[1:].partition("]")
        if not closing:
            return None
    else:
        host, colon, port_text = value.partition(":")
        rest = f"{colon}{port_text}"
    # Nothing after the host means no port; otherwise exactly ":<digits>" and nothing else.
    if not rest:
        return host, None, bracketed
    if not rest.startswith(":") or _PORT.fullmatch(rest[1:]) is None:
        return None
    return host, int(rest[1:]), bracketed


def _is_loopback_host(host: str, *, bracketed: bool) -> bool:
    """Return whether a split Host names this machine in one of the accepted spellings."""
    # A bracketed host is an IPv6 literal, and only ::1 itself (unzoned) is loopback.
    if bracketed:
        try:
            return ipaddress.IPv6Address(host) == _IPV6_LOOPBACK
        except ValueError:
            return False
    if host.lower() == _LOCALHOST:
        return True
    # IPv4Address parses only the strict dotted quad, so "127.1" and octal or hex forms fail.
    try:
        return ipaddress.IPv4Address(host) in _IPV4_LOOPBACK
    except ValueError:
        return False
