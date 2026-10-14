"""Write the text of a device's routing table and DNS settings, as the Propolis commands print it.

The Propolis checks read a device through fixed commands (`cat /proc/net/route`, `netstat -rn`,
`cat /etc/resolv.conf` and the like) and parse their text, so the tests need that text in the
exact formats the kernel and the BSD tools print: little-endian hex for Linux IPv4, big-endian hex
for Linux IPv6, a column table for macOS. These builders take readable rows (an interface, a
network, a metric) and print them the way the real tools would, and `linux_probes`/`macos_probes`
bundle the commands' outputs into the `ExecScript`s a scripted device answers with.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tunnel, shield and
    Propolis attestation tests.

Key invariants:
    - The text is what the real tools print, so a parser that passes here reads a real device.
    - Nothing here runs a command; the outputs are plain strings.

See Also:
    - hivemind.cell.tunnel.routes for the parsers these feed.
    - hivemind.cell.tunnel.check for the commands whose output these stand in for.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from builders.swarm.device import ExecScript

__all__ = [
    "RouteRow",
    "linux_probes",
    "linux_route",
    "linux_route6",
    "macos_netstat",
    "macos_probes",
    "resolv_conf",
    "scutil_dns",
]

RouteRow = tuple[str, str, int]  # Interface, network, metric.

LINUX_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"


def linux_route(interface: str, network: str, metric: int = 0, flags: int = 0x0003) -> str:
    """One row of `/proc/net/route`: hex flags, little-endian hex addresses, decimal metric."""
    net = ipaddress.IPv4Network(network)
    destination = net.network_address.packed[::-1].hex().upper()
    mask = net.netmask.packed[::-1].hex().upper()
    return f"{interface}\t{destination}\t0100000A\t{flags:04X}\t0\t0\t{metric}\t{mask}\t0\t0\t0"


def linux_route6(interface: str, network: str, metric: int = 1024, flags: int = 0x0003) -> str:
    """One row of `/proc/net/ipv6_route`: ten space-separated fields, big-endian hex."""
    net = ipaddress.IPv6Network(network)
    zeros = "0" * 32
    return (
        f"{net.network_address.packed.hex()} {net.prefixlen:02x} {zeros} 00 {zeros} "
        f"{metric:08x} 00000001 00000000 {flags:08x} {interface}"
    )


def macos_netstat(
    inet: Sequence[tuple[str, str, str, str]] = (),
    inet6: Sequence[tuple[str, str, str, str]] = (),
    *,
    with_counters: bool = False,
) -> str:
    """The text of `netstat -rn` on macOS.

    Args:
        inet: IPv4 rows as (destination, gateway, flags, interface).
        inet6: IPv6 rows in the same shape.
        with_counters: Print the Refs and Use columns newer macOS versions add before Netif.

    Returns:
        The two sections, each with the header row its columns need.
    """
    heading = "Destination Gateway Flags Refs Use Netif Expire"
    if not with_counters:
        heading = "Destination Gateway Flags Netif Expire"

    def row(fields: tuple[str, str, str, str]) -> str:
        destination, gateway, flags, interface = fields
        counters = " 12 340" if with_counters else ""
        return f"{destination} {gateway} {flags}{counters} {interface}"

    lines = ["Routing tables", "", "Internet:", heading, *(row(f) for f in inet)]
    lines += ["", "Internet6:", heading, *(row(f) for f in inet6)]
    return "\n".join(lines) + "\n"


def resolv_conf(*nameservers: str) -> str:
    """The text of `/etc/resolv.conf` naming these nameservers."""
    return "# Generated\nsearch lan\n" + "".join(f"nameserver {n}\n" for n in nameservers)


def scutil_dns(*nameservers: str) -> str:
    """The text of macOS `scutil --dns` naming these nameservers in its first resolver."""
    listed = "".join(f"  nameserver[{i}] : {n}\n" for i, n in enumerate(nameservers))
    return f"DNS configuration\n\nresolver #1\n{listed}  flags    : Request A records\n"


def linux_probes(
    routes: Sequence[RouteRow], routes6: Sequence[RouteRow] = (), nameservers: Sequence[str] = ()
) -> dict[str, ExecScript]:
    """The scripts a Linux device answers the three Propolis commands with."""
    table = "\n".join([LINUX_HEADER, *(linux_route(i, n, m) for i, n, m in routes)]) + "\n"
    table6 = "".join(linux_route6(i, n, m) + "\n" for i, n, m in routes6)
    return {
        "cat /proc/net/route": ExecScript(stdout=table.encode()),
        "cat /proc/net/ipv6_route": ExecScript(stdout=table6.encode()),
        "cat /etc/resolv.conf": ExecScript(stdout=resolv_conf(*nameservers).encode()),
    }


def macos_probes(
    inet: Sequence[tuple[str, str, str, str]], nameservers: Sequence[str] = ()
) -> dict[str, ExecScript]:
    """The scripts a macOS device answers the two Propolis commands with."""
    return {
        "netstat -rn": ExecScript(stdout=macos_netstat(inet).encode()),
        "scutil --dns": ExecScript(stdout=scutil_dns(*nameservers).encode()),
    }
