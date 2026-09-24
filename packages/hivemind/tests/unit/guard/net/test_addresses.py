"""Tests for hivemind.guard.net.addresses: which hosts and addresses reach the Hive Stand itself.

Fits into the Hive:
    Mirrors src/hivemind/guard/net/addresses.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.net.addresses for the module under test.
"""

from __future__ import annotations

import ipaddress

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.guard.net import (
    address_refusal,
    ip_literal,
    is_loopback_name,
    is_metadata_address,
    is_metadata_host,
    network_refusal,
    normalise_host,
    plain_address,
)

_OWN = frozenset({ipaddress.ip_address("192.168.1.20"), ipaddress.ip_address("2001:db8::20")})
# codingrules 14.3: a generous, deterministic example budget and no per-test deadline.
_SETTINGS = settings(max_examples=300, deadline=None)


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse one address literal."""
    return ipaddress.ip_address(text)


# ──────────────────────────────────────────────────────────────────────────────
# Reducing an address to its plain form
# ──────────────────────────────────────────────────────────────────────────────


def test_plain_address_reads_an_ipv4_mapped_address_as_the_ipv4_it_carries() -> None:
    assert plain_address(_ip("::ffff:127.0.0.1")) == _ip("127.0.0.1")


def test_plain_address_drops_an_ipv6_zone_id() -> None:
    assert plain_address(_ip("fe80::1%eth0")) == _ip("fe80::1")


def test_normalise_host_lowercases_and_drops_a_trailing_dot_and_a_zone() -> None:
    assert normalise_host("Example.COM.") == "example.com"
    assert normalise_host("fe80::1%eth0") == "fe80::1"
    assert normalise_host("fe80::1%25eth0") == "fe80::1"


def test_normalise_host_keeps_percent_encoding_in_a_name_whole() -> None:
    # Cutting a name at "%" would judge `exa` while the client connects somewhere else.
    assert normalise_host("exa%20mple.com") == "exa%20mple.com"


def test_ip_literal_parses_a_bracketed_or_zoned_literal_and_refuses_a_name() -> None:
    assert ip_literal("[::1]") == _ip("::1")
    assert ip_literal("fe80::1%eth0") == _ip("fe80::1")
    assert ip_literal("::ffff:10.0.0.1") == _ip("10.0.0.1")
    assert ip_literal("example.com") is None
    # A shorthand only a resolver understands is a name here, never an address.
    assert ip_literal("127.1") is None


# ──────────────────────────────────────────────────────────────────────────────
# Names and addresses that reach the Hive Stand
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST.", "db.localhost", "a.b.localhost"])
def test_localhost_and_every_name_under_it_is_a_loopback_name(host: str) -> None:
    assert is_loopback_name(host) is True


@pytest.mark.parametrize("host", ["notlocalhost", "localhost.example.com", "127.0.0.1"])
def test_a_name_that_only_looks_local_is_not_a_loopback_name(host: str) -> None:
    assert is_loopback_name(host) is False


@pytest.mark.parametrize(
    ("literal", "phrase"),
    [
        ("127.0.0.1", "a loopback address"),
        ("127.255.0.9", "a loopback address"),
        ("::1", "a loopback address"),
        ("::ffff:127.0.0.1", "a loopback address"),
        ("0.0.0.0", "an unspecified address"),  # noqa: S104  # SAFETY: judged, never bound.
        ("0.1.2.3", "an unspecified address"),
        ("::", "an unspecified address"),
        ("169.254.169.254", "a link-local address"),
        ("fe80::1", "a link-local address"),
        ("::ffff:169.254.1.1", "a link-local address"),
        ("192.168.1.20", "one of the Hive Stand's own addresses"),
        ("::ffff:192.168.1.20", "one of the Hive Stand's own addresses"),
    ],
)
def test_address_refusal_names_why_an_address_reaches_the_hive_stand(
    literal: str, phrase: str
) -> None:
    assert address_refusal(_ip(literal), _OWN) == phrase


@pytest.mark.parametrize("literal", ["93.184.216.34", "10.0.0.5", "2001:db8::99", "8.8.8.8"])
def test_address_refusal_allows_an_ordinary_address(literal: str) -> None:
    assert address_refusal(_ip(literal), _OWN) is None


@pytest.mark.parametrize(
    ("cidr", "phrase"),
    [
        ("127.0.0.0/8", "a loopback address"),
        ("0.0.0.0/0", "a loopback address"),
        ("::/0", "a loopback address"),
        ("169.254.0.0/24", "a link-local address"),
        ("::ffff:0:0/96", "a loopback address"),
        ("192.168.1.0/24", "one of the Hive Stand's own addresses"),
        ("127.0.0.1/32", "a loopback address"),
    ],
)
def test_network_refusal_refuses_a_range_that_can_reach_a_forbidden_address(
    cidr: str, phrase: str
) -> None:
    assert network_refusal(ipaddress.ip_network(cidr), _OWN) == phrase


def test_network_refusal_allows_a_range_that_reaches_nothing_forbidden() -> None:
    assert network_refusal(ipaddress.ip_network("10.0.0.0/8"), _OWN) is None
    assert network_refusal(ipaddress.ip_network("2001:db8:1::/48"), _OWN) is None


@given(value=st.integers(min_value=0x7F000000, max_value=0x7FFFFFFF))
@_SETTINGS
def test_every_ipv4_loopback_address_is_refused_plain_or_mapped(value: int) -> None:
    address = ipaddress.IPv4Address(value)
    mapped = ipaddress.IPv6Address(f"::ffff:{address}")

    assert address_refusal(address, frozenset()) == "a loopback address"
    assert address_refusal(mapped, frozenset()) == "a loopback address"


# ──────────────────────────────────────────────────────────────────────────────
# Cloud metadata endpoints
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    ["169.254.169.254", "fd00:ec2::254", "100.100.100.200", "metadata.google.internal", "fe80::9"],
)
def test_is_metadata_host_knows_the_metadata_names_and_addresses(host: str) -> None:
    assert is_metadata_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "10.0.0.1", "metadata.example.com"])
def test_is_metadata_host_refuses_nothing_else(host: str) -> None:
    assert is_metadata_host(host) is False


def test_is_metadata_address_judges_the_plain_form() -> None:
    assert is_metadata_address(_ip("::ffff:169.254.169.254")) is True
    assert is_metadata_address(_ip("203.0.113.10")) is False
