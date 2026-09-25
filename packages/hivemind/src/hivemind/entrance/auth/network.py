"""Name the network a device reaches the Entrance from, and render its address for the trail.

A request reaches the Hive Entrance (the Hive's one HTTP door) from a network address. Two things
are derived from it here, both pure. A device's **network** is what a session records and the
travel lock compares (ADR-0041): the /24 of an IPv4 address or the /64 of an IPv6 one, or, when
tailscaled reports that a peer's traffic is relayed rather than direct, ``derp:<region>`` (the
DERP relay region carrying it), since a relayed peer has no endpoint of its own to take a prefix
of. A **trail address** is the address as the Pheromone Trail (the Hive's audit log) records it:
the address itself when it looks like one, a fixed placeholder otherwise, so nothing a client
controls (a test client's host name, a forged header value) can put a line break or markup on the
trail. Every network is written one canonical way, so two sightings of one network always compare
equal.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by the
    enrolled-device model (``last_network``), the session records, the login and request checks
    and the travel lock. Calls into the standard library only.

Key invariants:
    - ``address_network`` and ``relay_network`` return only values ``is_device_network`` accepts,
      in their one canonical spelling, or None.
    - ``trail_address`` never returns text outside ``[0-9A-Za-z.:%_-[]]`` or longer than
      ``MAX_ADDRESS_CHARS``.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "travel lock".
    - hivemind.entrance.auth.travel for the lock that compares networks.
"""

from __future__ import annotations

import ipaddress
import re

IPV4_NETWORK_PREFIX = 24  # ADR-0041: a device's network is the /24 of an IPv4 address...
IPV6_NETWORK_PREFIX = 64  # ...and the /64 of an IPv6 one.
RELAY_PREFIX = "derp:"  # A relayed peer's network: its DERP region, e.g. "derp:nyc".
MAX_ADDRESS_CHARS = 64  # An IPv6 address with its zone fits; anything longer is not an address.
UNREADABLE_ADDRESS = "unreadable"  # Recorded in place of an address that is not one.
# What a network address (or a test client's host name) looks like: nothing that could smuggle a
# line break or markup onto the trail.
_ADDRESS = re.compile(rf"[0-9A-Za-z.:%_\-\[\]]{{1,{MAX_ADDRESS_CHARS}}}")
# A DERP region code as tailscaled reports it ("nyc", "fra", a custom "home-derp"): short,
# lowercase, and free of anything that could forge a second network value.
_RELAY_REGION = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")

__all__ = [
    "IPV4_NETWORK_PREFIX",
    "IPV6_NETWORK_PREFIX",
    "MAX_ADDRESS_CHARS",
    "RELAY_PREFIX",
    "UNREADABLE_ADDRESS",
    "address_network",
    "is_device_network",
    "relay_network",
    "trail_address",
]


def address_network(address: str) -> str | None:
    """Return the network ``address`` belongs to: its /24 (IPv4) or /64 (IPv6), canonical CIDR.

    Args:
        address: An IP address as a socket reports it; brackets and an IPv6 zone are allowed,
            and an IPv4-mapped IPv6 address counts as its IPv4 address.

    Returns:
        The network in CIDR form, e.g. ``"100.64.3.0/24"``, or None when ``address`` is not an
        IP address at all (a test client's host name).
    """
    # A zone ("%eth0") and URL brackets are spelling, not part of the address.
    bare = address.strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return None
    # An IPv4 peer reaching a dual-stack socket arrives as ::ffff:a.b.c.d; it is still that /24.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    prefix = IPV4_NETWORK_PREFIX if ip.version == 4 else IPV6_NETWORK_PREFIX
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def relay_network(region: str) -> str | None:
    """Return the network of a peer relayed through DERP region ``region``.

    Args:
        region: The region code tailscaled reports for the peer, e.g. ``"nyc"``.

    Returns:
        ``"derp:<region>"``, or None when ``region`` is empty or not a plain region code.
    """
    if _RELAY_REGION.fullmatch(region) is None:
        return None
    return f"{RELAY_PREFIX}{region}"


def is_device_network(value: str) -> bool:
    """Return whether ``value`` is a device network in its canonical spelling.

    Args:
        value: A candidate network: CIDR text or ``derp:<region>``.

    Returns:
        True for a canonical IPv4 /24, a canonical IPv6 /64 (no host bits either way) or a
        ``derp:<region>``; False for anything else.
    """
    if value.startswith(RELAY_PREFIX):
        return _RELAY_REGION.fullmatch(value.removeprefix(RELAY_PREFIX)) is not None
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError:
        return False
    prefix = IPV4_NETWORK_PREFIX if network.version == 4 else IPV6_NETWORK_PREFIX
    # One spelling per network: "fd7a:115c:a1e0:0::/64" would otherwise never equal its twin.
    return network.prefixlen == prefix and str(network) == value


def trail_address(address: str) -> str:
    """Return ``address`` as the trail records it: itself if it looks like one, else a placeholder.

    Args:
        address: The requesting address, as the listener reported it.

    Returns:
        ``address`` unchanged, or ``UNREADABLE_ADDRESS``.
    """
    return address if _ADDRESS.fullmatch(address) is not None else UNREADABLE_ADDRESS
