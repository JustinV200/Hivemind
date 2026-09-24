"""Validate and match `net` scopes: a host, a domain's subdomains, an IP literal or a network.

`net` (reaching the network) has a scope kind of its own, `ScopeKind.HOST` (ADR-0031), because
neither a glob nor a prefix over text can say what a network grant means: `net:*.example.com`
must cover `a.example.com` but never `badexample.com`, and `net:10.0.0.0/8` must cover the address
`10.1.2.3`, which shares no text prefix with it. A held scope is one of five shapes: `*` (any
host), an exact DNS name (compared case-insensitively), `*.<domain>` (every strict subdomain of
`<domain>`, on a label boundary), an IP literal (IPv4 or IPv6, compared as an address), or a CIDR
network (`10.0.0.0/8`, `fd00::/8`) that covers every address inside it. A `*` anywhere else is
invalid, so `net:10.0.0.*` or `net:api.*` fails loudly instead of silently meaning something.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by `hivemind.guard.capabilities.
    scopes` for the one family of kind HOST. Calls into the standard library only (`ipaddress`,
    `re`); nothing here ever resolves a name, so matching never touches the network.

Key invariants:
    - A needed scope at an enforcement point is concrete (a host or an address). When whole sets
      are compared (`CapabilitySet.attenuate`, `issubset`) a needed scope may be a pattern too;
      it is covered only by a held scope at least as wide, so every valid scope covers itself and
      nothing ever covers more than its held scope admits.
    - A DNS name and an address never cover each other: no lookup happens, so `net:example.com`
      says nothing about the addresses that name resolves to, and vice versa.
    - IPv4 and IPv6 never cover each other either; an IPv4-mapped IPv6 literal is IPv6.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the grammar.
    - hivemind.guard.capabilities.scopes for the other six kinds, and the dispatch to this one.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

ANY_HOST = "*"  # The held scope that covers every host and every address.
_SUBDOMAINS = "*."  # A leading "*." means every strict subdomain of the domain that follows.
_MAX_NAME_CHARS = 253  # RFC 1035's limit on a DNS name's text form, dots included.
# One DNS label once lowercased: letters, digits, "-" and "_" (seen in service records), 1-63
# characters (RFC 1035). IDNs are written in their ASCII (punycode) form.
_LABEL = re.compile(r"[a-z0-9_-]{1,63}")

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

__all__ = ["ANY_HOST", "host_covers", "host_error"]


@dataclass(frozen=True, slots=True)
class _Host:
    """One `net` scope, parsed: exactly one of the four shapes below is set, or `any_host`."""

    any_host: bool = False  # "*": every host and every address.
    name: str | None = None  # An exact DNS name, lowercased.
    parent: str | None = None  # "*.<parent>": every strict subdomain of this lowercased name.
    network: _Network | None = None  # An IP literal (a one-address network) or a CIDR network.


def host_error(scope: str) -> str | None:
    """Explain why `scope` is not a valid `net` scope, or return None when it is.

    Args:
        scope: The text after `net:`; already known to be non-empty.

    Returns:
        None for `*`, a DNS name, `*.<DNS name>`, an IP literal or a CIDR network with no host
        bits set; otherwise a short phrase saying what is wrong, for an error message.
    """
    return None if _parse(scope) is not None else f"{scope!r} is not a host, *.domain, IP or CIDR"


def host_covers(held: str, needed: str) -> bool:
    """Decide whether a held `net` scope covers a needed one.

    Args:
        held: A valid `net` scope the bee holds.
        needed: A valid `net` scope an action needs: normally a concrete host or address, or a
            pattern when a whole set is being compared (see the module's key invariants).

    Returns:
        True if every host or address `needed` can name is one `held` also admits.
    """
    held_host, needed_host = _parse(held), _parse(needed)
    # Both were validated when their Capability was built; a None here means a caller built one
    # around validation, and refusing is the only safe answer.
    if held_host is None or needed_host is None:
        return False
    # By the held shape: "*" admits everything, an exact name only itself, a subdomain pattern
    # only what lies beneath it, and an address range only addresses inside it.
    if held_host.any_host:
        return True
    if held_host.name is not None:
        return held_host.name == needed_host.name
    if held_host.parent is not None:
        return _under(needed_host, held_host.parent)
    return _inside(needed_host.network, held_host.network)


def _parse(scope: str) -> _Host | None:
    """Parse one `net` scope into its shape, or return None when it has none of the five."""
    # The two wildcard shapes first: no address or DNS name ever contains a "*".
    if scope == ANY_HOST:
        return _Host(any_host=True)
    if scope.startswith(_SUBDOMAINS):
        parent = _dns_name(scope[len(_SUBDOMAINS) :])
        return _Host(parent=parent) if parent is not None else None
    # An address before a name: "10.0.0.1" would also pass as a name of four numeric labels,
    # and must compare as an address instead.
    network = _ip_network(scope)
    if network is not None:
        return _Host(network=network)
    name = _dns_name(scope)
    return _Host(name=name) if name is not None else None


def _dns_name(text: str) -> str | None:
    """Return `text` lowercased when it is a DNS name, else None (a stray "*" included)."""
    lowered = text.lower()
    if not lowered or len(lowered) > _MAX_NAME_CHARS:
        return None
    # Every dot-separated label must be well formed: this also rejects an empty label (a leading,
    # trailing or doubled dot) and any "*", which only ever appears as the leading "*." shape.
    if all(_LABEL.fullmatch(label) for label in lowered.split(".")):
        return lowered
    return None


def _ip_network(text: str) -> _Network | None:
    """Return `text` as a network: an IP literal as a one-address network, or a CIDR as written.

    A CIDR with host bits set (`10.0.0.1/8`) is refused rather than silently widened to its
    network, since the writer almost certainly meant something narrower.
    """
    try:
        return ipaddress.ip_network(text, strict=True)
    except ValueError:
        return None  # Not an address or a network: an answer, not an error; `_parse` goes on.


def _under(needed: _Host, parent: str) -> bool:
    """Decide whether `needed` names only strict subdomains of `parent` (on a label boundary)."""
    suffix = f".{parent}"
    if needed.name is not None:
        return needed.name.endswith(suffix)
    # A needed "*.<x>" names strict subdomains of <x>: all of them are strict subdomains of
    # `parent` exactly when <x> is `parent` itself or one of its own subdomains.
    if needed.parent is not None:
        return needed.parent == parent or needed.parent.endswith(suffix)
    return False  # An address or "*" is never "a subdomain of" anything.


def _inside(needed: _Network | None, held: _Network | None) -> bool:
    """Decide whether the needed network lies wholly inside the held one (same IP version).

    A DNS name or a subdomain pattern (None here) never lies inside an address range, and IPv4
    and IPv6 never cover each other (`subnet_of` itself refuses mixed versions), so only two
    same-version pairs can ever answer True.
    """
    if isinstance(needed, ipaddress.IPv4Network) and isinstance(held, ipaddress.IPv4Network):
        return needed.subnet_of(held)
    if isinstance(needed, ipaddress.IPv6Network) and isinstance(held, ipaddress.IPv6Network):
        return needed.subnet_of(held)
    return False
