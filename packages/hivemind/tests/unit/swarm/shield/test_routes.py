"""Tests for hivemind.swarm.shield.routes: reading a device's routing table and DNS settings.

Fits into the Hive:
    Mirrors src/hivemind/swarm/shield/routes.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The readers are pure functions over the text real tools print, so each test
    feeds them that text (built in the formats the kernel and the BSD tools use) and reads the
    routes back. The property that matters is that a line the parser cannot read is skipped and
    never raised, because a device's text is hostile until proven otherwise.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.shield.routes for the module under test.
"""

from __future__ import annotations

import ipaddress

import pytest
from builders.swarm import (
    linux_route,
    linux_route6,
    macos_netstat,
    resolv_conf,
    scutil_dns,
)

from hivemind.swarm.shield import (
    Route,
    effective_interface,
    is_tunnel,
    parse_linux_routes,
    parse_linux_routes6,
    parse_macos_routes,
    parse_nameservers,
)
from hivemind.swarm.shield.routes import TUNNEL_PREFIXES

_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"


def _net(text: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """A network from its text."""
    return ipaddress.ip_network(text)


def _addr(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """An address from its text."""
    return ipaddress.ip_address(text)


def _linux(*rows: str) -> str:
    """A `/proc/net/route` with these rows under its header."""
    return "\n".join([_HEADER, *rows]) + "\n"


# ──────────────────────────────────────────────────────────────────────────────
# Linux IPv4
# ──────────────────────────────────────────────────────────────────────────────


def test_linux_ipv4_rows_become_routes_with_the_network_interface_and_decimal_metric() -> None:
    text = _linux(
        linux_route("tun0", "0.0.0.0/1"),
        linux_route("eth0", "0.0.0.0/0", metric=100),
        linux_route("eth0", "192.168.0.0/24", metric=100, flags=0x0001),
        linux_route("eth0", "10.1.2.3/32", metric=7),
    )

    assert parse_linux_routes(text) == (
        Route(_net("0.0.0.0/1"), "tun0", 0),
        Route(_net("0.0.0.0/0"), "eth0", 100),
        Route(_net("192.168.0.0/24"), "eth0", 100),
        Route(_net("10.1.2.3/32"), "eth0", 7),
    )


def test_a_linux_route_that_is_down_or_rejects_packets_is_not_a_route() -> None:
    text = _linux(
        linux_route("eth0", "0.0.0.0/0", flags=0x0000),  # Not UP.
        linux_route("eth0", "10.0.0.0/8", flags=0x0203),  # UP, but REJECT.
        linux_route("eth0", "172.16.0.0/12", flags=0x0001),
    )

    assert [r.network for r in parse_linux_routes(text)] == [_net("172.16.0.0/12")]


@pytest.mark.parametrize(
    "line",
    [
        "eth0 0000 0000",  # Too few fields.
        "eth0\tZZZZZZZZ\t0100000A\t0003\t0\t0\t0\t00000000\t0\t0\t0",  # Not hex.
        "eth0\t00000000\t0100000A\tXXXX\t0\t0\t0\t00000000\t0\t0\t0",  # Flags not hex.
        "eth0\t00000000\t0100000A\t0003\t0\t0\tfast\t00000000\t0\t0\t0",  # Metric not a number.
        "",
    ],
)
def test_a_linux_line_that_cannot_be_read_is_skipped_and_the_rest_still_parse(line: str) -> None:
    text = _linux(line, linux_route("tun0", "0.0.0.0/1"))

    assert [r.interface for r in parse_linux_routes(text)] == ["tun0"]


def test_an_empty_linux_table_has_no_routes() -> None:
    assert parse_linux_routes("") == () and parse_linux_routes(_HEADER) == ()


# ──────────────────────────────────────────────────────────────────────────────
# Linux IPv6
# ──────────────────────────────────────────────────────────────────────────────


def test_linux_ipv6_rows_become_routes_and_the_loopback_bookkeeping_is_left_out() -> None:
    text = "".join(
        line + "\n"
        for line in (
            linux_route6("tun0", "::/0", metric=1),
            linux_route6("eth0", "fe80::/64"),
            linux_route6("lo", "::1/128", flags=0x0001),
            linux_route6("eth0", "2001:db8::/32", flags=0x0000),  # Down.
            linux_route6("eth0", "2001:db9::/32", flags=0x0203),  # Reject.
        )
    )

    assert parse_linux_routes6(text) == (
        Route(_net("::/0"), "tun0", 1),
        Route(_net("fe80::/64"), "eth0", 1024),
    )


@pytest.mark.parametrize("line", ["too few fields", "zz 00 " + "0 " * 8 + "eth0", ""])
def test_an_unreadable_ipv6_line_is_skipped(line: str) -> None:
    text = line + "\n" + linux_route6("tun0", "::/0") + "\n"

    assert [r.interface for r in parse_linux_routes6(text)] == ["tun0"]


# ──────────────────────────────────────────────────────────────────────────────
# macOS
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("with_counters", [False, True])
def test_macos_netstat_rows_expand_to_networks_and_name_the_interface(with_counters: bool) -> None:
    text = macos_netstat(
        inet=[
            ("default", "192.168.1.1", "UGScg", "en0"),
            ("0/1", "10.8.0.1", "UGSc", "utun4"),
            ("128.0/1", "10.8.0.1", "UGSc", "utun4"),
            ("127", "127.0.0.1", "UCS", "lo0"),
            ("10.8/24", "10.8.0.2", "UGSc", "utun4"),
            ("192.168.1/24", "link#6", "UCS", "en0"),
            ("1.2.3.4", "10.8.0.1", "UGHS", "utun4"),
        ],
        inet6=[
            ("default", "fe80::%utun4", "UGcg", "utun4"),
            ("fe80::%en0/64", "link#6", "UcI", "en0"),
        ],
        with_counters=with_counters,
    )

    assert {(str(r.network), r.interface) for r in parse_macos_routes(text)} == {
        ("0.0.0.0/0", "en0"),
        ("0.0.0.0/1", "utun4"),
        ("128.0.0.0/1", "utun4"),
        ("127.0.0.0/8", "lo0"),
        ("10.8.0.0/24", "utun4"),
        ("192.168.1.0/24", "en0"),
        ("1.2.3.4/32", "utun4"),
        ("::/0", "utun4"),
        ("fe80::/64", "en0"),
    }


@pytest.mark.parametrize("flags", ["UGRSc", "UGBSc", "Gc"])
def test_a_macos_route_that_rejects_blackholes_or_is_not_up_is_not_a_route(flags: str) -> None:
    text = macos_netstat(inet=[("10.9/16", "10.8.0.1", flags, "utun4")])

    assert parse_macos_routes(text) == ()


def test_macos_text_with_no_interface_column_or_unreadable_rows_yields_what_it_can() -> None:
    no_netif = "Internet:\nDestination Gateway Flags\ndefault 1.1.1.1 UGSc\n"
    garbage = macos_netstat(
        inet=[("not-an-ip/99", "x", "UGSc", "en0"), ("10/8", "x", "UGSc", "en0")]
    )

    assert parse_macos_routes(no_netif) == ()
    assert [str(r.network) for r in parse_macos_routes(garbage)] == ["10.0.0.0/8"]
    assert parse_macos_routes("") == ()


# ──────────────────────────────────────────────────────────────────────────────
# Nameservers
# ──────────────────────────────────────────────────────────────────────────────


def test_nameservers_are_read_in_order_once_each_from_resolv_conf() -> None:
    text = resolv_conf("10.8.0.1", "1.1.1.1", "10.8.0.1", "fe80::1%eth0")

    assert parse_nameservers(text) == (_addr("10.8.0.1"), _addr("1.1.1.1"), _addr("fe80::1"))


def test_nameservers_are_read_from_scutil_dns() -> None:
    assert parse_nameservers(scutil_dns("10.8.0.1", "10.8.0.2")) == (
        _addr("10.8.0.1"),
        _addr("10.8.0.2"),
    )


def test_a_hostname_a_comment_and_a_search_line_are_not_nameservers() -> None:
    text = "# nameserver 9.9.9.9\nsearch lan\nnameserver dns.example.net\nnameserver 8.8.8.8\n"

    assert parse_nameservers(text) == (_addr("8.8.8.8"),)
    assert parse_nameservers("") == ()


# ──────────────────────────────────────────────────────────────────────────────
# Which interface a packet leaves by
# ──────────────────────────────────────────────────────────────────────────────


def test_the_longest_prefix_wins_even_over_a_lower_metric() -> None:
    routes = (Route(_net("0.0.0.0/0"), "eth0", 0), Route(_net("10.0.0.0/8"), "tun0", 500))

    assert effective_interface(routes, _addr("10.1.1.1")) == "tun0"
    assert effective_interface(routes, _addr("8.8.8.8")) == "eth0"


def test_a_vpns_split_default_shadows_the_real_default_route() -> None:
    routes = (
        Route(_net("0.0.0.0/0"), "eth0", 100),
        Route(_net("0.0.0.0/1"), "tun0", 0),
        Route(_net("128.0.0.0/1"), "tun0", 0),
    )

    assert effective_interface(routes, _addr("1.1.1.1")) == "tun0"
    assert effective_interface(routes, _addr("203.0.113.1")) == "tun0"


def test_on_a_tie_of_prefix_the_lowest_metric_then_the_first_listed_wins() -> None:
    routes = (
        Route(_net("0.0.0.0/0"), "eth0", 100),
        Route(_net("0.0.0.0/0"), "wlan0", 50),
        Route(_net("0.0.0.0/0"), "usb0", 50),
    )

    assert effective_interface(routes, _addr("8.8.8.8")) == "wlan0"


def test_the_address_family_is_never_crossed_and_an_uncovered_address_has_no_interface() -> None:
    routes = (Route(_net("::/0"), "tun0", 0), Route(_net("10.0.0.0/8"), "eth0", 0))

    assert effective_interface(routes, _addr("8.8.8.8")) is None
    assert effective_interface(routes, _addr("2606:4700:4700::1111")) == "tun0"
    assert effective_interface((), _addr("8.8.8.8")) is None


@pytest.mark.parametrize("prefix", TUNNEL_PREFIXES)
def test_an_interface_a_vpn_client_creates_is_a_tunnel(prefix: str) -> None:
    assert is_tunnel(prefix + "0")


@pytest.mark.parametrize("name", ["eth0", "en0", "wlan0", "docker0", "lo", "br-lan", ""])
def test_any_other_interface_is_not_a_tunnel(name: str) -> None:
    assert not is_tunnel(name)
