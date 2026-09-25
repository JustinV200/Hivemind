"""Judge DNS names for exposure: public_url's host, a certificate's names, and the relying party.

Remote always means TLS on a DNS name (ADR-0041): browsers give passkeys, WebCrypto and push only
to a secure context, and WebAuthn refuses an IP address as a relying party. Three name questions
follow, answered here as pure functions. What host does ``public_url`` name, in the one form a
browser sends and a certificate lists (lowercase ASCII, no trailing dot), and is it a DNS name at
all? Does the server certificate cover that host, by the rules browsers apply (RFC 6125: an exact
name, or a wildcard standing for exactly one whole left-most label)? And is ``rp_id`` that host or
a parent domain of it, as WebAuthn requires? ``httpx``'s URL parser (the one the Entrance's own
clients use) splits the URL, and the label syntax is checked here, because that parser is lenient.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Called by
    ``hivemind.entrance.expose.plan``. Calls into ``httpx`` (URL parsing) and the standard library.

Key invariants:
    - ``public_host`` returns only a name ``is_dns_name`` accepts, or None; never an IP literal
      and never ``localhost`` or a name under it.
    - A wildcard never matches more than one label, never a partial label, and never directly
      under a top-level domain.

See Also:
    - RFC 6125 section 6.4 for certificate name matching.
    - https://www.w3.org/TR/webauthn-3/#rp-id for the relying party rule.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

import httpx

MAX_DNS_NAME_CHARS = 253  # RFC 1035's limit on a whole name, without the trailing dot.
_MAX_PORT = 65535  # The highest TCP port; httpx accepts larger numbers without complaint.
_LOCALHOST = "localhost"  # RFC 6761: this name and every name under it are always loopback.
_WILDCARD_PREFIX = "*."  # A wildcard certificate name: "*." then the base it covers.
_MIN_WILDCARD_BASE_DOTS = 1  # "*.example.com" at the least: never "*.com", a whole TLD.
# Public suffixes a remote setup documented here plausibly meets (all on the Public Suffix List):
# Tailscale's MagicDNS domain, the common tunnel services and a dynamic-DNS provider. Each names
# many unrelated operators' hosts, so none can ever be a relying party (see is_public_suffix).
_KNOWN_PUBLIC_SUFFIXES = frozenset(
    {
        "ts.net",
        "beta.tailscale.net",
        "trycloudflare.com",
        "cfargotunnel.com",
        "ngrok.io",
        "ngrok.app",
        "ngrok.dev",
        "ngrok-free.app",
        "ngrok-free.dev",
        "loca.lt",
        "duckdns.org",
    }
)
# One DNS label (RFC 1123): letters, digits and hyphens, 1 to 63 of them, no hyphen at either end.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_HAS_LETTER = re.compile(r"[a-z]")  # A top-level domain is never all digits (RFC 3696 2).

__all__ = [
    "MAX_DNS_NAME_CHARS",
    "certificate_covers",
    "describe_public_host",
    "is_dns_name",
    "is_public_suffix",
    "is_same_or_parent_domain",
    "public_host",
    "public_origin",
]


def public_host(url: str) -> str | None:
    """Return the DNS name an https URL names, in canonical form, or None when it names none.

    Args:
        url: ``[entrance] public_url``; the schema already insists on ``https://``.

    Returns:
        The host, lowercase ASCII (an internationalised name in its ``xn--`` form) without a
        trailing dot; None when the URL does not parse, carries credentials or a port outside
        1..65535, or its host is empty, an IP address, a loopback name or not a DNS name.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return None
    # Credentials before the host, or an impossible port, mean the URL is not what it seems.
    if parsed.userinfo or (parsed.port is not None and not 0 < parsed.port <= _MAX_PORT):
        return None
    host = parsed.raw_host.decode("ascii", errors="replace").lower().removesuffix(".")
    return host if is_dns_name(host) else None


def public_origin(url: str) -> str:
    """Return the origin a browser sends for pages served from ``url``: the one CORS allows.

    Args:
        url: A URL ``public_host`` accepted.

    Returns:
        ``https://<host>`` plus ``:<port>`` only when the port is not 443, exactly as a browser
        serialises an origin.
    """
    parsed = httpx.URL(url)
    host = parsed.raw_host.decode("ascii", errors="replace").lower().removesuffix(".")
    # httpx already drops a default port (443 for https), so any port left is a real one.
    return f"https://{host}" if parsed.port is None else f"https://{host}:{parsed.port}"


def describe_public_host(url: str) -> str:
    """Say what host a URL names, for a refusal, without echoing anything else in it.

    Args:
        url: ``[entrance] public_url``, which may carry credentials or a query by mistake.

    Returns:
        One sentence naming the host as parsed, or saying it has none or carries credentials;
        never the credentials, the path or the query themselves.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return "public_url does not parse as a URL."
    # Credentials before the host are named as present, never shown: they may be a password.
    if parsed.userinfo:
        return "public_url carries credentials before its host."
    host = parsed.raw_host.decode("ascii", errors="replace")
    return f"public_url's host is {host!r}." if host else "public_url names no host."


def is_public_suffix(domain: str) -> bool:
    """Return whether ``domain`` is a public suffix a browser refuses as a WebAuthn relying party.

    Not the whole Public Suffix List: a single-label name (a top-level domain) plus the suffixes
    this Hive's documented remote setups meet (Tailscale's MagicDNS, the common tunnel and dynamic
    DNS providers), so the usual mistake is refused at start with a sentence rather than by the
    browser at the first ceremony. A suffix outside this set still fails there, loudly.

    Args:
        domain: A lowercase name without a trailing dot, e.g. an ``rp_id``.

    Returns:
        True when ``domain`` is one label or one of the known suffixes; False otherwise.
    """
    return "." not in domain or domain in _KNOWN_PUBLIC_SUFFIXES


def is_dns_name(host: str) -> bool:
    """Return whether ``host`` is a DNS name a certificate and a relying party can use.

    Args:
        host: A lowercase host without a trailing dot.

    Returns:
        True for a syntactically valid name of LDH labels whose top-level label is not all
        digits; False for an empty or oversized name, an IP literal, ``localhost`` or a name
        under it, and anything else.
    """
    # An IP literal and the loopback names are hosts, but never names a remote device can use.
    if not host or len(host) > MAX_DNS_NAME_CHARS or _is_ip_literal(host):
        return False
    if host == _LOCALHOST or host.endswith(f".{_LOCALHOST}"):
        return False
    labels = host.split(".")
    return all(_LABEL.fullmatch(label) for label in labels) and bool(_HAS_LETTER.search(labels[-1]))


def certificate_covers(dns_names: Iterable[str], host: str) -> bool:
    """Return whether any of a certificate's DNS names covers ``host``, as a browser judges it.

    Args:
        dns_names: The certificate's subjectAltName DNS entries.
        host: The canonical host from ``public_host``.

    Returns:
        True when one entry equals ``host`` (case and a trailing dot aside) or is a wildcard
        whose ``*`` stands for exactly ``host``'s whole left-most label.
    """
    return any(_name_matches(name.lower().removesuffix("."), host) for name in dns_names)


def is_same_or_parent_domain(domain: str, host: str) -> bool:
    """Return whether ``domain`` is ``host`` itself or a parent domain of it (WebAuthn's RP rule).

    Public suffixes (``co.uk``, ``ts.net``) are not detected here; a browser refuses such a
    relying party at the first ceremony.

    Args:
        domain: The relying party id, e.g. ``example.com``; an internationalised one must be
            written in its ``xn--`` form.
        host: The canonical host from ``public_host``, e.g. ``hive.example.com``.

    Returns:
        True for an equal name or a whole-label suffix; ``ample.com`` is not a parent of
        ``example.com``.
    """
    candidate = domain.lower().removesuffix(".")
    return bool(candidate) and (host == candidate or host.endswith(f".{candidate}"))


def _name_matches(pattern: str, host: str) -> bool:
    """Match one lowercase certificate name against ``host`` (RFC 6125 section 6.4.3)."""
    # An exact name is compared whole; a partial wildcard ("f*.example.com") never equals a host.
    if not pattern.startswith(_WILDCARD_PREFIX):
        return pattern == host
    base = pattern.removeprefix(_WILDCARD_PREFIX)
    label, dot, rest = host.partition(".")
    # "*" is exactly one non-empty left-most label, and the base must be below a TLD.
    return (
        bool(label) and bool(dot) and rest == base and base.count(".") >= (_MIN_WILDCARD_BASE_DOTS)
    )


def _is_ip_literal(host: str) -> bool:
    """Return whether ``host`` parses as an IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
