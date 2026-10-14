"""Provide the Propolis judges: four pure checks over a Cell's routes and DNS settings.

The Propolis tier means the Cell's traffic leaves through a VPN tunnel the operator already runs,
and HiveMind trusts that only after looking (it never installs, starts or reroutes one). Four
checks decide it, each a pure function over the routes and nameservers that
`hivemind.cell.tunnel.routes` read from the Cell. `default_route_via_tunnel`: a packet to a
public address in each half of the IPv4 space would leave by a tunnel interface. `dns_via_tunnel`:
the Cell has nameservers, none of them a local stub resolver the check cannot see past, and
each would be reached through a tunnel. `no_direct_default_route`: no IPv6 path to the public
internet avoids the tunnel (a Cell with no IPv6 route passes). `direct_egress_refused`: a spread
of well-known public addresses, none of which is ever contacted, would all leave by the tunnel;
this is a routing-table lookup rather than a packet, because sending one around the tunnel would be
the very leak the check looks for. A check that cannot be answered (an unreadable table, no
nameserver) fails: the tier is verified only when every check positively passes.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell.tunnel`, below
    both `swarm` (a Real Cell's tier) and `hive` (a Virtual Cell's) so that the two judge one way.
    Called by `hivemind.cell.tunnel.check` with the parsed output of its fixed commands. Calls
    into `hivemind.cell.tunnel.routes` and the standard library only.

Key invariants:
    - Every judge is pure and total: the same routes always give the same result, and no input
      raises.
    - A result's `reason` is a short stable word; it never holds an address, an interface name or
      any text the Cell produced, because the result is recorded on the trail.
    - `ShieldReport.is_verified` is True only for a non-empty report in which every check passed.

See Also:
    - README.md "Comb Shield level" for what the Propolis tier promises.
    - hivemind.cell.tunnel.check for the fixed commands whose output these judge.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from hivemind.cell.tunnel.routes import (
    IpAddress,
    Route,
    effective_interface,
    is_tunnel,
)

CHECK_DEFAULT_ROUTE = "default_route_via_tunnel"
CHECK_DNS = "dns_via_tunnel"
CHECK_IPV6 = "no_direct_default_route"
CHECK_EGRESS = "direct_egress_refused"

# One public address in each half of the IPv4 space, so a VPN's split default (`0.0.0.0/1` and
# `128.0.0.0/1`) is judged by what actually carries each half.
HALF_PROBES = (ipaddress.IPv4Address("1.1.1.1"), ipaddress.IPv4Address("203.0.113.1"))
# Well-known public hosts spread across the address space. They are looked up in the routing table
# and never contacted.
EGRESS_PROBES = tuple(
    ipaddress.IPv4Address(text)
    for text in (
        "1.1.1.1",
        "8.8.8.8",
        "9.9.9.9",
        "104.16.0.1",
        "142.250.1.1",
        "151.101.1.1",
        "203.0.113.1",
        "208.67.222.222",
    )
)
IPV6_PROBE = ipaddress.IPv6Address("2606:4700:4700::1111")

__all__ = [
    "CHECK_DEFAULT_ROUTE",
    "CHECK_DNS",
    "CHECK_EGRESS",
    "CHECK_IPV6",
    "CheckResult",
    "ShieldReport",
    "judge_all",
    "judge_default_route",
    "judge_dns",
    "judge_egress",
    "judge_ipv6",
]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One Propolis check's answer.

    Attributes:
        name: The check's stable name.
        has_passed: Whether the Cell positively passed it.
        reason: A short stable word saying why, never an address or Cell text.
    """

    name: str
    has_passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ShieldReport:
    """The answers of every check run on one Cell.

    Attributes:
        checks: Each check's result, in the order they ran; empty when none could run.
    """

    checks: tuple[CheckResult, ...]

    @property
    def is_verified(self) -> bool:
        """Whether there is at least one check and every check passed."""
        return bool(self.checks) and all(check.has_passed for check in self.checks)

    @property
    def failed(self) -> tuple[str, ...]:
        """The names of the checks that did not pass."""
        return tuple(check.name for check in self.checks if not check.has_passed)


def _leaves_by_tunnel(routes: tuple[Route, ...], address: IpAddress) -> bool | None:
    """True if a packet to `address` leaves by a tunnel, False if not, None if unrouted."""
    interface = effective_interface(routes, address)
    return None if interface is None else is_tunnel(interface)


def _has_ipv4(routes: tuple[Route, ...]) -> bool:
    """Whether any IPv4 route was read: a missing table fails every check that needs it."""
    return any(route.network.version == 4 for route in routes)


def judge_default_route(routes: tuple[Route, ...]) -> CheckResult:
    """Judge that both halves of the IPv4 space leave the Cell by a tunnel."""
    if not _has_ipv4(routes):
        return CheckResult(CHECK_DEFAULT_ROUTE, False, "table_unreadable")
    answers = [_leaves_by_tunnel(routes, probe) for probe in HALF_PROBES]
    if None in answers:
        return CheckResult(CHECK_DEFAULT_ROUTE, False, "no_default_route")
    if not all(answers):
        return CheckResult(CHECK_DEFAULT_ROUTE, False, "direct_default_route")
    return CheckResult(CHECK_DEFAULT_ROUTE, True, "tunnel")


def judge_dns(routes: tuple[Route, ...], nameservers: tuple[IpAddress, ...]) -> CheckResult:
    """Judge that the Cell has nameservers and each is reached through a tunnel."""
    if not _has_ipv4(routes):
        return CheckResult(CHECK_DNS, False, "table_unreadable")
    if not nameservers:
        return CheckResult(CHECK_DNS, False, "no_nameservers")
    # A loopback resolver is a local stub (systemd-resolved, dnsmasq): where it forwards cannot
    # be seen from the routing table, so it cannot be verified and the check fails closed.
    if any(address.is_loopback for address in nameservers):
        return CheckResult(CHECK_DNS, False, "local_stub_resolver")
    if not all(_leaves_by_tunnel(routes, address) for address in nameservers):
        return CheckResult(CHECK_DNS, False, "dns_not_via_tunnel")
    return CheckResult(CHECK_DNS, True, "tunnel")


def judge_ipv6(routes: tuple[Route, ...]) -> CheckResult:
    """Judge that no IPv6 path to the public internet avoids the tunnel."""
    answer = _leaves_by_tunnel(routes, IPV6_PROBE)
    if answer is None:
        return CheckResult(CHECK_IPV6, True, "no_ipv6_route")
    return CheckResult(CHECK_IPV6, answer, "tunnel" if answer else "direct_ipv6_route")


def judge_egress(routes: tuple[Route, ...]) -> CheckResult:
    """Judge that none of a spread of public addresses would be reached other than by a tunnel."""
    if not _has_ipv4(routes):
        return CheckResult(CHECK_EGRESS, False, "table_unreadable")
    if not all(_leaves_by_tunnel(routes, probe) for probe in EGRESS_PROBES):
        return CheckResult(CHECK_EGRESS, False, "direct_egress")
    return CheckResult(CHECK_EGRESS, True, "tunnel")


def judge_all(routes: tuple[Route, ...], nameservers: tuple[IpAddress, ...]) -> ShieldReport:
    """Run the four checks over the Cell's routes and nameservers."""
    return ShieldReport(
        checks=(
            judge_default_route(routes),
            judge_dns(routes, nameservers),
            judge_ipv6(routes),
            judge_egress(routes),
        )
    )
