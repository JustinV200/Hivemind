"""Tests for hivemind.swarm.shield.judges: the four checks a Propolis device must pass.

Fits into the Hive:
    Mirrors src/hivemind/swarm/shield/judges.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Each judge is a pure function over routes and nameservers, so each test builds
    the table of a device in one situation (a full tunnel, a split default, a leak, a stub
    resolver, nothing readable) and reads the verdict. The rule they all share is that a check
    which cannot be answered fails, because the tier is granted only on a positive pass.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.shield.judges for the module under test.
    - README.md "Comb Shield level" for the tier these verify.
"""

from __future__ import annotations

import ipaddress

import pytest

from hivemind.swarm.shield import (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_EGRESS,
    CHECK_IPV6,
    CheckResult,
    ShieldReport,
    judge_all,
    judge_default_route,
    judge_dns,
    judge_egress,
    judge_ipv6,
)
from hivemind.swarm.shield.routes import IpAddress, Route

_TUNNEL_DNS = (ipaddress.ip_address("10.8.0.1"),)


def _route(network: str, interface: str, metric: int = 0) -> Route:
    """One route."""
    return Route(ipaddress.ip_network(network), interface, metric)


# A VPN client's full tunnel: both halves of IPv4 through tun0 shadow the real default route.
FULL_TUNNEL = (
    _route("0.0.0.0/1", "tun0"),
    _route("128.0.0.0/1", "tun0"),
    _route("0.0.0.0/0", "eth0", 100),
    _route("192.168.0.0/24", "eth0", 100),
    _route("10.8.0.0/24", "tun0"),
)
LEAKING = (_route("0.0.0.0/0", "eth0", 100), _route("10.8.0.0/24", "tun0"))
HALF_TUNNEL = (_route("0.0.0.0/1", "tun0"),)


def test_a_device_with_a_full_tunnel_passes_all_four_checks() -> None:
    report = judge_all(FULL_TUNNEL, _TUNNEL_DNS)

    assert report.is_verified and report.failed == ()
    assert [c.name for c in report.checks] == [
        CHECK_DEFAULT_ROUTE,
        CHECK_DNS,
        CHECK_IPV6,
        CHECK_EGRESS,
    ]


def test_a_device_whose_default_route_leaves_by_the_network_fails_the_route_dns_and_egress() -> (
    None
):
    report = judge_all(LEAKING, (ipaddress.ip_address("8.8.8.8"),))

    assert not report.is_verified
    assert report.failed == (CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS)
    reasons = {c.name: c.reason for c in report.checks}
    assert reasons[CHECK_DEFAULT_ROUTE] == "direct_default_route"
    assert reasons[CHECK_DNS] == "dns_not_via_tunnel"
    assert reasons[CHECK_EGRESS] == "direct_egress"


# ──────────────────────────────────────────────────────────────────────────────
# default_route_via_tunnel
# ──────────────────────────────────────────────────────────────────────────────


def test_both_halves_of_the_ipv4_space_must_leave_by_a_tunnel() -> None:
    assert judge_default_route(FULL_TUNNEL) == CheckResult(CHECK_DEFAULT_ROUTE, True, "tunnel")


def test_a_tunnel_carrying_only_one_half_leaves_the_other_without_a_route() -> None:
    result = judge_default_route(HALF_TUNNEL)

    assert result == CheckResult(CHECK_DEFAULT_ROUTE, False, "no_default_route")


def test_no_ipv4_routes_at_all_means_the_table_could_not_be_read() -> None:
    assert judge_default_route(()) == CheckResult(CHECK_DEFAULT_ROUTE, False, "table_unreadable")
    only_v6 = (_route("::/0", "tun0"),)
    assert judge_default_route(only_v6).reason == "table_unreadable"


# ──────────────────────────────────────────────────────────────────────────────
# dns_via_tunnel
# ──────────────────────────────────────────────────────────────────────────────


def test_every_nameserver_must_be_reached_through_a_tunnel() -> None:
    assert judge_dns(FULL_TUNNEL, _TUNNEL_DNS) == CheckResult(CHECK_DNS, True, "tunnel")
    mixed: tuple[IpAddress, ...] = (*_TUNNEL_DNS, ipaddress.ip_address("192.168.0.53"))
    assert judge_dns(FULL_TUNNEL, mixed) == CheckResult(CHECK_DNS, False, "dns_not_via_tunnel")


def test_a_nameserver_no_route_reaches_fails_the_check() -> None:
    assert (
        judge_dns(HALF_TUNNEL, (ipaddress.ip_address("200.1.1.1"),)).reason == "dns_not_via_tunnel"
    )


def test_no_nameserver_fails_the_check() -> None:
    assert judge_dns(FULL_TUNNEL, ()) == CheckResult(CHECK_DNS, False, "no_nameservers")


@pytest.mark.parametrize("stub", ["127.0.0.53", "127.0.0.1", "::1"])
def test_a_local_stub_resolver_cannot_be_seen_past_so_it_fails(stub: str) -> None:
    result = judge_dns(FULL_TUNNEL, (ipaddress.ip_address(stub),))

    assert result == CheckResult(CHECK_DNS, False, "local_stub_resolver")


def test_dns_without_a_readable_table_fails() -> None:
    assert judge_dns((), _TUNNEL_DNS).reason == "table_unreadable"


# ──────────────────────────────────────────────────────────────────────────────
# no_direct_default_route (IPv6)
# ──────────────────────────────────────────────────────────────────────────────


def test_a_device_with_no_ipv6_route_passes_the_ipv6_check() -> None:
    assert judge_ipv6(FULL_TUNNEL) == CheckResult(CHECK_IPV6, True, "no_ipv6_route")
    assert judge_ipv6(()).has_passed


def test_an_ipv6_default_through_the_tunnel_passes_and_one_through_the_network_fails() -> None:
    assert judge_ipv6((_route("::/0", "tun0"),)) == CheckResult(CHECK_IPV6, True, "tunnel")
    assert judge_ipv6((_route("::/0", "eth0"),)) == CheckResult(
        CHECK_IPV6, False, "direct_ipv6_route"
    )


# ──────────────────────────────────────────────────────────────────────────────
# direct_egress_refused
# ──────────────────────────────────────────────────────────────────────────────


def test_a_spread_of_public_addresses_must_all_leave_by_the_tunnel() -> None:
    assert judge_egress(FULL_TUNNEL) == CheckResult(CHECK_EGRESS, True, "tunnel")


def test_one_public_address_that_leaves_directly_fails_the_check() -> None:
    routes = (*FULL_TUNNEL, _route("142.250.0.0/16", "eth0"))

    assert judge_egress(routes) == CheckResult(CHECK_EGRESS, False, "direct_egress")


def test_a_public_address_with_no_route_fails_the_check() -> None:
    assert judge_egress(HALF_TUNNEL).reason == "direct_egress"


def test_egress_without_a_readable_table_fails() -> None:
    assert judge_egress(()).reason == "table_unreadable"


# ──────────────────────────────────────────────────────────────────────────────
# The report
# ──────────────────────────────────────────────────────────────────────────────


def test_a_report_with_no_checks_is_not_verified() -> None:
    assert not ShieldReport(()).is_verified and ShieldReport(()).failed == ()


def test_a_report_is_verified_only_when_every_check_passed() -> None:
    passing, failing = CheckResult("a", True, "tunnel"), CheckResult("b", False, "leak")

    assert ShieldReport((passing,)).is_verified
    assert not ShieldReport((passing, failing)).is_verified
    assert ShieldReport((passing, failing)).failed == ("b",)


def test_a_result_never_carries_an_address_or_an_interface_name() -> None:
    report = judge_all(LEAKING, (ipaddress.ip_address("8.8.8.8"),))

    text = " ".join(f"{c.name} {c.reason}" for c in report.checks)
    assert "8.8.8.8" not in text and "eth0" not in text and "tun0" not in text
