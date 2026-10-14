"""Tests for hivemind.hive.night_veil.judges.resolver: where a Night Veil Cell's DNS resolver is.

The old check passed on any `/etc/resolv.conf` that could be read. The judge now places every
`nameserver`: Tor's own DNS port on loopback (`DNSPort ...:53` in the torrc) passes, an address a
packet to which leaves by a tunnel interface passes, and anything else fails: a public or LAN
resolver, a loopback stub that is not Tor's (Docker's embedded resolver, systemd-resolved's), no
resolver at all, or a routing table that could not be read. The tunnel half is the Propolis tier's
own judge, so these tests show the two tiers agree about what "through the tunnel" means.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/judges/resolver.py (codingrules section 3). The input is
    the text the fixed reads print, built by `builders.swarm`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.judges.resolver for the module under test.
    - tests/unit/cell/tunnel/test_judges.py for the tunnel judge it reuses.
"""

from __future__ import annotations

import ipaddress

import pytest
from builders.swarm import linux_route, linux_route6, resolv_conf

from hivemind.hive.night_veil.judges import judge_resolver, tor_dns_addresses
from hivemind.hive.night_veil.results import CheckStatus

_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
_TORRC = "SocksPort 127.0.0.1:9050\nDNSPort 127.0.0.1:53\nClientOnly 1\n"
_TUNNEL = (("tun0", "0.0.0.0/1"), ("tun0", "128.0.0.0/1"), ("eth0", "0.0.0.0/0"))
_LEAKING = (("eth0", "0.0.0.0/0"), ("tun0", "10.8.0.0/24"))


def _texts(
    *nameservers: str,
    routes: tuple[tuple[str, str], ...] = _TUNNEL,
    torrc: str = "",
    routes6: str = "",
) -> dict[str, str]:
    """The four reads of a Cell with these resolvers, this routing table and this torrc."""
    table = "\n".join([_HEADER, *(linux_route(i, n) for i, n in routes)]) + "\n"
    return {
        "dns": resolv_conf(*nameservers),
        "routes": table,
        "routes6": routes6,
        "torrc": torrc,
    }


def _verdict(texts: dict[str, str]) -> tuple[CheckStatus, str]:
    result = judge_resolver(texts)
    return result.status, result.detail


# ──────────────────────────────────────────────────────────────────────────────
# Green: Tor's own DNS port, or a resolver only the tunnel reaches.
# ──────────────────────────────────────────────────────────────────────────────


def test_a_resolver_reached_through_the_tunnel_is_green() -> None:
    assert _verdict(_texts("10.8.0.1")) == (CheckStatus.PASS, "tunnel")


def test_tors_own_dns_port_on_loopback_is_green_with_no_routing_table_at_all() -> None:
    # Nothing leaves the Cell for a lookup Tor itself answers, so the routes are not asked.
    texts = _texts("127.0.0.1", torrc=_TORRC) | {"routes": "", "routes6": ""}

    assert _verdict(texts) == (CheckStatus.PASS, "tor_dns_port")


def test_a_dns_port_given_alone_binds_the_ipv4_loopback_and_is_tors_resolver() -> None:
    assert _verdict(_texts("127.0.0.1", torrc="DNSPort 53\n"))[0] is CheckStatus.PASS


def test_tors_dns_port_and_a_tunnel_resolver_together_are_green() -> None:
    texts = _texts("127.0.0.1", "10.8.0.1", torrc=_TORRC)

    assert _verdict(texts) == (CheckStatus.PASS, "tor_dns_port_and_tunnel")


def test_an_ipv6_tunnel_route_does_not_disturb_an_ipv4_resolver_through_the_tunnel() -> None:
    texts = _texts("10.8.0.1", routes6=linux_route6("tun0", "::/0") + "\n")

    assert _verdict(texts)[0] is CheckStatus.PASS


# ──────────────────────────────────────────────────────────────────────────────
# Red: everything the old check let through, and what a Cell can get wrong.
# ──────────────────────────────────────────────────────────────────────────────


def test_a_public_resolver_that_leaves_by_the_network_is_a_leak_the_old_check_passed() -> None:
    # The old check passed on any readable resolv.conf, this one included.
    texts = _texts("8.8.8.8", routes=_LEAKING)

    assert _verdict(texts) == (CheckStatus.FAIL, "dns_not_via_tunnel")


def test_a_lan_resolver_beside_a_tunnel_one_is_red() -> None:
    texts = _texts("10.8.0.1", "192.168.1.1", routes=(("eth0", "192.168.1.0/24"), *_TUNNEL))

    assert _verdict(texts) == (CheckStatus.FAIL, "dns_not_via_tunnel")


@pytest.mark.parametrize("stub", ["127.0.0.53", "127.0.0.11", "127.0.0.1"])
def test_a_loopback_stub_that_is_not_tors_cannot_be_seen_past_and_is_red(stub: str) -> None:
    assert _verdict(_texts(stub)) == (CheckStatus.FAIL, "local_stub_resolver")


def test_a_dns_port_that_is_not_53_is_not_a_resolver_a_resolv_conf_can_name() -> None:
    # A resolv.conf cannot name port 5353, so 127.0.0.1 is whatever answers on 53: a stub.
    texts = _texts("127.0.0.1", torrc="DNSPort 127.0.0.1:5353\n")

    assert _verdict(texts) == (CheckStatus.FAIL, "local_stub_resolver")


def test_tors_resolver_beside_a_public_one_is_red() -> None:
    texts = _texts("127.0.0.1", "8.8.8.8", routes=_LEAKING, torrc=_TORRC)

    assert _verdict(texts) == (CheckStatus.FAIL, "dns_not_via_tunnel")


@pytest.mark.parametrize("dns", ["", "# Generated\nsearch lan\n"])
def test_no_resolver_to_place_is_red(dns: str) -> None:
    assert _verdict(_texts("10.8.0.1") | {"dns": dns}) == (CheckStatus.FAIL, "no_nameservers")


def test_a_routing_table_that_could_not_be_read_fails_a_resolver_that_needs_it() -> None:
    texts = _texts("10.8.0.1") | {"routes": "", "routes6": ""}

    assert _verdict(texts) == (CheckStatus.FAIL, "table_unreadable")


def test_a_read_left_out_entirely_is_red_not_an_error() -> None:
    assert judge_resolver({}).status is CheckStatus.FAIL


def test_a_result_never_carries_an_address_or_an_interface() -> None:
    detail = judge_resolver(_texts("8.8.8.8", routes=_LEAKING)).detail

    assert "8.8.8.8" not in detail and "eth0" not in detail


# ──────────────────────────────────────────────────────────────────────────────
# tor_dns_addresses: reading the torrc.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("torrc", "expected"),
    [
        ("DNSPort 53", {"127.0.0.1"}),
        ("DNSPort 127.0.0.1:53", {"127.0.0.1"}),
        ("dnsport 127.0.0.1:53 IsolateClientAddr", {"127.0.0.1"}),  # Case-insensitive, flags.
        ("  DNSPort 127.0.0.2:53", {"127.0.0.2"}),  # Any loopback address.
        ("DNSPort [::1]:53", {"::1"}),
        ("DNSPort 127.0.0.1:53\nDNSPort [::1]:53", {"127.0.0.1", "::1"}),
        ("# DNSPort 127.0.0.1:53", set()),  # A comment.
        ("DNSPort 5353", set()),  # Not the port a resolver is asked on.
        ("DNSPort 0.0.0.0:53", set()),  # Open to others, not the Cell's own loopback.
        ("DNSPort 192.168.1.5:53", set()),
        ("DNSPort auto", set()),
        ("DNSPort", set()),
        ("", set()),
    ],
)
def test_the_torrc_names_the_loopback_addresses_tor_answers_dns_on(
    torrc: str, expected: set[str]
) -> None:
    assert tor_dns_addresses(torrc) == {ipaddress.ip_address(a) for a in expected}
