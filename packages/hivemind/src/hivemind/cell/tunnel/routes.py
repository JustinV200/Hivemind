"""Provide the routing-table readers behind the Propolis checks: pure parsers and one lookup.

A Cell in the Propolis tier (the Comb Shield tier that requires a VPN) keeps its tunnel running
itself; HiveMind only looks, with read-only commands, and refuses to place work if the tunnel is
not where it should be (README: "verify the tunnel before placing work and never install, start or
reroute one"). What it looks at is the Cell's routing table and its DNS settings, as the text of
fixed commands: `/proc/net/route`, `/proc/net/ipv6_route` and `/etc/resolv.conf` on Linux,
`netstat -rn` and `scutil --dns` on macOS. This module turns that text into `Route`s and
nameserver addresses and answers the one question every check asks, `effective_interface`: which
interface would a packet to this address leave by? That is the longest matching prefix, the lowest
metric on a tie, so it reads a VPN's split default (`0.0.0.0/1` and `128.0.0.0/1`, which shadow
the real default route) correctly instead of mistaking the shadowed route for a leak. Everything
here is a pure function over text: no command runs, nothing is sent, and an unreadable table is
an empty tuple, which the judges then fail closed on.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell.tunnel`. It sits
    here, below both `swarm` (a Real Cell's tier) and `hive` (a Virtual Cell's), so that the two
    Layer 3 siblings, which may not import each other, read a routing table one way. Used by the
    judges in `hivemind.cell.tunnel.judges`. Calls into the standard library only.

Key invariants:
    - Every function is pure and total over any text: a line it cannot read is skipped, never
      raised, and no output is ever logged or recorded, since a Cell's routes are its own.
    - A route that is down or a reject or blackhole is not a route.
    - `effective_interface` prefers the longest prefix, then the lowest metric, then the first.
    - An interface is a tunnel only if its name starts with a known tunnel prefix; an unknown
      name is not one, so a surprising Cell fails the check rather than passing it.

See Also:
    - README.md "Comb Shield level" for the Propolis tier.
    - hivemind.cell.tunnel.judges for the checks built on these readers.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

# Interface names a VPN client creates. A name that matches none of these is treated as a
# physical or unknown interface, which is the safe reading for "does traffic leave by a tunnel".
TUNNEL_PREFIXES = ("tun", "tap", "wg", "utun", "ppp", "ipsec", "vpn", "nordlynx", "proton")

_RTF_UP = 0x0001  # Linux route flag: the route is usable.
_RTF_REJECT = 0x0200  # Linux route flag: packets to it are refused, not sent.
_ZONE = re.compile(r"%[^/\s]+")  # An IPv6 zone suffix (`%en0`), which is not part of the network.
_NAMESERVER = re.compile(r"^\s*nameserver(?:\[\d+\])?\s*[: ]\s*(\S+)", re.MULTILINE)

__all__ = [
    "TUNNEL_PREFIXES",
    "IpAddress",
    "IpNetwork",
    "Route",
    "effective_interface",
    "is_tunnel",
    "parse_linux_routes",
    "parse_linux_routes6",
    "parse_macos_routes",
    "parse_nameservers",
]

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True, slots=True)
class Route:
    """One usable routing-table entry: where packets to a network leave the Cell."""

    network: IpNetwork
    interface: str
    metric: int


def is_tunnel(interface: str) -> bool:
    """Whether an interface name is one a VPN client creates."""
    return interface.startswith(TUNNEL_PREFIXES)


def effective_interface(routes: tuple[Route, ...], address: IpAddress) -> str | None:
    """Return the interface a packet to `address` would leave by, or None when no route covers it.

    Args:
        routes: The Cell's usable routes, of one or both address families.
        address: The destination.

    Returns:
        The interface of the longest matching prefix, the lowest metric on a tie, the first listed
        on a further tie; None when nothing matches.
    """
    covering = [
        route
        for route in routes
        if route.network.version == address.version and address in route.network
    ]
    if not covering:
        return None
    best = max(covering, key=lambda route: (route.network.prefixlen, -route.metric))
    return best.interface


def parse_nameservers(text: str) -> tuple[IpAddress, ...]:
    """Read the nameserver addresses out of `/etc/resolv.conf` or `scutil --dns` text.

    Args:
        text: The command's standard output.

    Returns:
        Each distinct valid address, in the order listed; a zone suffix (`%eth0`) is dropped.
    """
    found: list[IpAddress] = []
    for match in _NAMESERVER.finditer(text):
        try:
            address = ipaddress.ip_address(match.group(1).split("%", 1)[0])
        except ValueError:
            continue  # A hostname or garbage: not an address we can look up a route for.
        if address not in found:
            found.append(address)
    return tuple(found)


def parse_linux_routes(text: str) -> tuple[Route, ...]:
    """Read the IPv4 routes of `/proc/net/route`: little-endian hex, one route per row."""
    routes: list[Route] = []
    for line in text.splitlines()[1:]:  # The first row is the column header.
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            # The kernel prints the flags in hex but the metric in decimal.
            flags, metric = int(fields[3], 16), int(fields[6])
            destination = int.from_bytes(bytes.fromhex(fields[1]), "little")
            mask = int.from_bytes(bytes.fromhex(fields[7]), "little")
            network = ipaddress.IPv4Network((destination, mask.bit_count()), strict=False)
        except ValueError:
            continue
        if flags & _RTF_UP and not flags & _RTF_REJECT:
            routes.append(Route(network, fields[0], metric))
    return tuple(routes)


def parse_linux_routes6(text: str) -> tuple[Route, ...]:
    """Read the IPv6 routes of `/proc/net/ipv6_route`: big-endian hex, ten fields per row."""
    routes: list[Route] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            flags, metric = int(fields[8], 16), int(fields[5], 16)
            destination = int(fields[0], 16)
            network = ipaddress.IPv6Network((destination, int(fields[1], 16)), strict=False)
        except ValueError:
            continue
        # The loopback interface carries the table's local and anycast bookkeeping, not egress.
        if flags & _RTF_UP and not flags & _RTF_REJECT and fields[9] != "lo":
            routes.append(Route(network, fields[9], metric))
    return tuple(routes)


def parse_macos_routes(text: str) -> tuple[Route, ...]:
    """Read the routes of macOS `netstat -rn`: an `Internet:` and an `Internet6:` section."""
    routes: list[Route] = []
    section = ""
    column: int | None = None  # Where "Netif" sits in this section's header row.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("Internet:", "Internet6:"):
            section, column = stripped, None
        elif stripped.startswith("Destination"):
            headings = stripped.split()
            column = headings.index("Netif") if "Netif" in headings else None
        elif section and column is not None:
            route = _macos_route(stripped.split(), column, section == "Internet6:")
            if route is not None:
                routes.append(route)
    return tuple(routes)


def _macos_route(fields: list[str], column: int, is_ipv6: bool) -> Route | None:
    """Read one `netstat -rn` row into a Route, or None for a row that is not a usable route."""
    if len(fields) <= column or "U" not in fields[2] or "R" in fields[2] or "B" in fields[2]:
        return None
    network = _macos_network(fields[0], is_ipv6)
    if network is None:
        return None
    return Route(network, fields[column].split("%", 1)[0], 0)


def _macos_network(destination: str, is_ipv6: bool) -> IpNetwork | None:
    """Expand a `netstat -rn` destination (`default`, `10.8/24`, `127`, a host) to a network."""
    try:
        if destination == "default":
            return ipaddress.ip_network("::/0" if is_ipv6 else "0.0.0.0/0")
        if is_ipv6:
            return ipaddress.ip_network(_ZONE.sub("", destination), strict=False)
        address, _, length = destination.partition("/")
        octets = address.split(".")
        padded = ".".join([*octets, *["0"] * (4 - len(octets))])
        # netstat abbreviates a network by dropping its trailing zero octets, and a bare short
        # form (`127`) means the classful prefix those octets imply.
        prefix = int(length) if length else 8 * len(octets)
        return ipaddress.ip_network(f"{padded}/{prefix}", strict=False)
    except ValueError:
        return None
