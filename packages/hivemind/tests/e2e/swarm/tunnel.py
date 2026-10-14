"""Give a device container the footprint of a VPN its operator runs, and read what it changed.

A Propolis device is one whose operator already runs a tunnel: HiveMind verifies it before it
places work and never installs, starts or reroutes one (README, "Comb Shield level"). A test of
that needs a device that has a tunnel, or does not, as a person arranged it. `raise_tunnel` does
what a VPN client does to a Linux machine, with the commands a person would type: a `tun` device,
an address on it, the default route through it and a nameserver that only it reaches. No packet
crosses it (the test needs the routing table and the resolver to say "tunnel", not an exit node),
and the container's own network, which the packet's link to the Hive Stand uses, stays on its
connected route, as a real client leaves the route to its VPN server. `network_state` reads back
exactly what the Propolis checks read, so a scenario can show that HiveMind left it as the
operator arranged it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the Propolis scenario;
    builds on `e2e.swarm.container`. The container must have been started with `NET_ADMIN`.

Key invariants:
    - Every command is the operator's, run in the container by the test, never by the Hive.
    - `network_state` is a pure read: the routing table, the IPv6 routing table and the resolver.

See Also:
    - hivemind.swarm.shield for the checks that read this state.
    - README.md "Comb Shield level" for the Propolis tier on a Real Cell.
"""

from __future__ import annotations

from typing import NamedTuple

from e2e.swarm.container import Container

TUNNEL_DEVICE = "tun0"
TUNNEL_ADDRESS = "10.8.0.2/24"  # The device's end of the tunnel, as an OpenVPN client is given one.
# The tunnel's far end: on the tunnel's subnet, so reached through it.
TUNNEL_NAMESERVER = "10.8.0.1"
TUN_NODE = "/dev/net/tun"
RESOLVER_FILE = "/etc/resolv.conf"

__all__ = ["RESOLVER_FILE", "TUNNEL_DEVICE", "NetworkState", "network_state", "raise_tunnel"]


class NetworkState(NamedTuple):
    """What the Propolis checks read from a device: its routes and its resolver."""

    routes: str
    routes6: str
    resolver: str


def raise_tunnel(container: Container) -> None:
    """Run the commands a VPN client runs on the device: a `tun` device, its address, the routes.

    Args:
        container: A device started with `NET_ADMIN`.
    """
    # A container has no /dev/net/tun until something makes it, which a VPN client's installer does.
    container.exec(["mkdir", "-p", "/dev/net"])
    if not container.exists(TUN_NODE):
        container.exec(["mknod", TUN_NODE, "c", "10", "200"])
    container.exec(["ip", "tuntap", "add", "dev", TUNNEL_DEVICE, "mode", "tun"])
    container.exec(["ip", "addr", "add", TUNNEL_ADDRESS, "dev", TUNNEL_DEVICE])
    container.exec(["ip", "link", "set", TUNNEL_DEVICE, "up"])
    # The default route moves to the tunnel; the connected route of the container's own network
    # (the packet's way to the Hive Stand) is more specific, so it keeps working.
    container.exec(["ip", "route", "replace", "default", "dev", TUNNEL_DEVICE])
    # In place, because Docker keeps /etc/resolv.conf as a bind mount it will not let be replaced.
    container.exec(["sh", "-c", f"echo 'nameserver {TUNNEL_NAMESERVER}' > {RESOLVER_FILE}"])


def network_state(container: Container) -> NetworkState:
    """Read the routing tables and the resolver of a device, as the Propolis checks do.

    Args:
        container: The device.

    Returns:
        The IPv4 routing table, the IPv6 one and `/etc/resolv.conf`, as text.
    """
    return NetworkState(
        routes=container.exec(["cat", "/proc/net/route"]).stdout,
        routes6=container.exec(["cat", "/proc/net/ipv6_route"], check=False).stdout,
        resolver=container.exec(["cat", RESOLVER_FILE]).stdout,
    )
