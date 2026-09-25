"""Tests for hivemind.entrance.auth.network: a device's network and a trail-safe address.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/network.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.network for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.entrance.auth.network import (
    UNREADABLE_ADDRESS,
    address_network,
    is_device_network,
    relay_network,
    trail_address,
)


@pytest.mark.parametrize(
    ("address", "network"),
    [
        ("100.64.3.7", "100.64.3.0/24"),
        ("fd7a:115c:a1e0::7", "fd7a:115c:a1e0::/64"),
        ("[fd7a:115c:a1e0::7]", "fd7a:115c:a1e0::/64"),
        ("fe80::1%eth0", "fe80::/64"),
        ("::ffff:100.64.3.7", "100.64.3.0/24"),
        ("testclient", None),
    ],
)
def test_address_network_takes_the_24_or_the_64_of_an_address(
    address: str, network: str | None
) -> None:
    assert address_network(address) == network


def test_relay_network_names_a_plain_region_and_refuses_anything_else() -> None:
    assert relay_network("nyc") == "derp:nyc"
    assert relay_network("home-derp") == "derp:home-derp"
    assert relay_network("") is None
    assert relay_network("NYC\nderp:fra") is None


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("100.64.3.0/24", True),
        ("fd7a:115c:a1e0::/64", True),
        ("derp:nyc", True),
        ("100.64.3.0/16", False),  # Not a device's /24.
        ("100.64.3.7/24", False),  # Host bits set.
        ("fd7a:115c:a1e0:0::/64", False),  # Not the canonical spelling.
        ("derp:", False),
        ("100.64.3.7", False),
    ],
)
def test_is_device_network_accepts_only_canonical_networks(value: str, accepted: bool) -> None:
    assert is_device_network(value) is accepted


def test_trail_address_keeps_an_address_and_replaces_anything_else() -> None:
    assert trail_address("100.64.0.7") == "100.64.0.7"
    assert trail_address("[fd7a::1]") == "[fd7a::1]"
    assert trail_address("evil\naddress") == UNREADABLE_ADDRESS
    assert trail_address("a" * 65) == UNREADABLE_ADDRESS
