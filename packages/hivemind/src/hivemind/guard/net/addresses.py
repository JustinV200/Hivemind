"""Classify hosts and addresses: loopback, unspecified, link-local, metadata, the Hive Stand's own.

Two Guard floors judge where a bee's network traffic would go (ADR-0039, ADR-0041): the Hive-state
floor refuses every bee a host that reaches the Hive Stand itself (a loopback name or address, the
unspecified address that some stacks send to the local host, a link-local address where cloud
metadata services live, or one of the Hive Stand's own addresses), and the Night Veil location
floor refuses a Night Veil task a host that is a cloud metadata endpoint. Both read the same small
set of pure predicates, collected here so the capability-string check (`net:127.0.0.1`) and the
resolved-address check the HTTP tool makes (`net:127.1` resolving to 127.0.0.1) can never
disagree about what counts. Every address is reduced first (`plain_address`): an IPv4-mapped IPv6
address is judged as the IPv4 address it carries, and an IPv6 zone id is dropped, so
`::ffff:127.0.0.1` and `fe80::1%eth0` get no second chance. Nothing here resolves a name; a name
is only ever judged by its own spelling (`localhost`, a name under `.localhost`, a known metadata
name), which is why the HTTP tool also resolves before it asks the Guard.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.net`. Read by
    `hivemind.guard.policy.floors` (the Hive-state and Night Veil floors), by
    `hivemind.guard.policy.hive_state` (the Hive Stand's own addresses) and by
    `hivemind.workers.tools.http` (which name it may skip resolving). Calls into the standard
    library only (`ipaddress`).

Key invariants:
    - Pure and total: no function here performs I/O or raises on any input string.
    - Every check reduces an address with `plain_address` before comparing it, so an IPv4-mapped
      form is never judged differently from the IPv4 address it maps.
    - A name never matches an address and vice versa: `localhost` is refused by its spelling,
      `127.0.0.1` by its value; resolving one into the other is the caller's job.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Bees never touch
      the Hive's own state".
    - hivemind.guard.net.resolve for the resolver seam that turns a name into addresses.
    - hivemind.entrance.push.destinations for the webhook guard that first used this approach.
"""

from __future__ import annotations

import ipaddress

LOCALHOST = "localhost"  # RFC 6761: this name, and every name under it, is always loopback.
_ZONE_SEPARATOR = "%"  # An IPv6 literal's zone id ("fe80::1%eth0"); dropped before any check.
# RFC 1122 "this network": is_unspecified covers only 0.0.0.0, but nothing listens anywhere in
# 0.0.0.0/8, and some stacks deliver such a destination to the local host.
_THIS_NETWORK = ipaddress.ip_network("0.0.0.0/8")
# Cloud metadata services reached by name: GCP's two names and AWS's legacy instance-data names.
# Each resolves to a link-local metadata address, but a name is refused on its spelling alone so a
# Night Veil goal cannot even ask for one.
METADATA_HOST_NAMES = frozenset(
    {"metadata.google.internal", "metadata.goog", "instance-data", "instance-data.ec2.internal"}
)
# Metadata services outside link-local space: AWS's IPv6 IMDS (a ULA address) and Alibaba
# Cloud's 100.100.100.200 (inside the shared address space). Every link-local address counts too.
METADATA_ADDRESSES = frozenset(
    {ipaddress.ip_address("fd00:ec2::254"), ipaddress.ip_address("100.100.100.200")}
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address  # Either family, as ipaddress parses it.
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network  # A CIDR range of either family.

__all__ = [
    "LOCALHOST",
    "METADATA_ADDRESSES",
    "METADATA_HOST_NAMES",
    "IPAddress",
    "IPNetwork",
    "address_refusal",
    "ip_literal",
    "is_loopback_name",
    "is_metadata_address",
    "is_metadata_host",
    "network_refusal",
    "normalise_host",
    "plain_address",
]

# The ranges `network_refusal` refuses a CIDR scope for overlapping, with the phrase each earns.
# IPv4-mapped IPv6 forms are listed beside their IPv4 range, since an IPv6 CIDR is never reduced.
_FORBIDDEN_NETWORKS: tuple[tuple[IPNetwork, str], ...] = (
    (ipaddress.ip_network("127.0.0.0/8"), "a loopback address"),
    (ipaddress.ip_network("::1/128"), "a loopback address"),
    (ipaddress.ip_network("::ffff:127.0.0.0/104"), "a loopback address"),
    (_THIS_NETWORK, "an unspecified address"),
    (ipaddress.ip_network("::/128"), "an unspecified address"),
    (ipaddress.ip_network("::ffff:0.0.0.0/104"), "an unspecified address"),
    (ipaddress.ip_network("169.254.0.0/16"), "a link-local address"),
    (ipaddress.ip_network("fe80::/10"), "a link-local address"),
    (ipaddress.ip_network("::ffff:169.254.0.0/112"), "a link-local address"),
)


def plain_address(address: IPAddress) -> IPAddress:
    """Reduce an address to what every check compares: IPv4 for an IPv4-mapped IPv6, no zone.

    Args:
        address: Any IPv4 or IPv6 address, possibly carrying an IPv6 zone id.

    Returns:
        The IPv4 address an IPv4-mapped IPv6 address carries; otherwise the same address with
        any zone id dropped.
    """
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return mapped
        # Rebuilding from the integer drops a zone id, which only link-local addresses carry.
        return ipaddress.IPv6Address(int(address))
    return address


def normalise_host(host: str) -> str:
    """Return a host the way every check compares it: lowercase, no trailing dot, no zone id.

    Args:
        host: A host as a URL parser or a capability scope spells it.

    Returns:
        The comparable spelling; an IPv6 literal keeps its address and loses its zone id. In a
        name, a `%` is percent-encoding (`exa%20mple.com`) and stays, so the name is judged as
        written rather than cut short at the `%`.
    """
    lowered = host.lower().rstrip(".")
    # Only an IPv6 literal (the one host form with a colon) carries a zone id.
    if ":" in lowered:
        return lowered.split(_ZONE_SEPARATOR, 1)[0]
    return lowered


def ip_literal(host: str) -> IPAddress | None:
    """Return `host` as a plain address when it is an IP literal, else None (it is a name).

    Args:
        host: A host, optionally bracketed (`[::1]`) or zoned (`fe80::1%eth0`).

    Returns:
        The address, reduced by `plain_address`; None for anything that is not a literal.
    """
    text = normalise_host(host.strip("[]"))
    try:
        return plain_address(ipaddress.ip_address(text))
    except ValueError:
        return None  # A name, not an address: an answer, not an error.


def is_loopback_name(host: str) -> bool:
    """Return whether `host` is `localhost` or a name under it (RFC 6761, loopback by definition).

    Args:
        host: A host name, in any case, with or without a trailing dot.

    Returns:
        True for `localhost` and every `*.localhost` name; False for anything else.
    """
    name = normalise_host(host)
    return name == LOCALHOST or name.endswith(f".{LOCALHOST}")


def address_refusal(address: IPAddress, own_addresses: frozenset[IPAddress]) -> str | None:
    """Say why a bee may never reach `address`, or return None when nothing forbids it.

    Args:
        address: The address a request would connect to.
        own_addresses: The Hive Stand's own addresses, already reduced by `plain_address`.

    Returns:
        A short phrase ("a loopback address", "an unspecified address", "a link-local
        address", "one of the Hive Stand's own addresses") for a forbidden address; None
        otherwise.
    """
    plain = plain_address(address)
    # Most specific first: 0.0.0.0/8 and :: reach the local host on some stacks.
    if plain.is_unspecified or plain in _THIS_NETWORK:
        return "an unspecified address"
    if plain.is_loopback:
        return "a loopback address"
    # 169.254.0.0/16 and fe80::/10: where cloud metadata services answer.
    if plain.is_link_local:
        return "a link-local address"
    if plain in own_addresses:
        return "one of the Hive Stand's own addresses"
    return None


def network_refusal(network: IPNetwork, own_addresses: frozenset[IPAddress]) -> str | None:
    """Say why a bee may never reach some address inside `network`, or return None.

    A one-address network is judged exactly as that address (`address_refusal`); a wider range
    is refused when it overlaps any forbidden range or contains one of the Hive Stand's own
    addresses, because some address inside it could be reached.

    Args:
        network: A CIDR range (or a one-address network) a capability scope names.
        own_addresses: The Hive Stand's own addresses, already reduced by `plain_address`.

    Returns:
        The phrase for the first forbidden address the range can reach; None when it reaches
        none.
    """
    if network.num_addresses == 1:
        return address_refusal(network.network_address, own_addresses)
    # Overlap is only ever asked within one address family: ipaddress refuses a mixed pair.
    for forbidden, phrase in _FORBIDDEN_NETWORKS:
        if forbidden.version == network.version and forbidden.overlaps(network):
            return phrase
    if any(own in network for own in own_addresses if own.version == network.version):
        return "one of the Hive Stand's own addresses"
    return None


def is_metadata_address(address: IPAddress) -> bool:
    """Return whether `address` is a cloud metadata endpoint: link-local, or a known one.

    Args:
        address: Any address.

    Returns:
        True for every link-local address and every entry of `METADATA_ADDRESSES`.
    """
    plain = plain_address(address)
    return plain.is_link_local or plain in METADATA_ADDRESSES


def is_metadata_host(host: str) -> bool:
    """Return whether `host` names a cloud metadata endpoint, by its spelling alone.

    Args:
        host: A host name or an IP literal, as a capability scope or a task's needs spell it.

    Returns:
        True for a known metadata name (`METADATA_HOST_NAMES`) or a metadata address literal.
    """
    literal = ip_literal(host)
    if literal is not None:
        return is_metadata_address(literal)
    return normalise_host(host) in METADATA_HOST_NAMES
