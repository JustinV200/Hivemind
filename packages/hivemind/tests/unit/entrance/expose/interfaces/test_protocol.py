"""Tests for hivemind.entrance.expose.interfaces.protocol: the zone rule.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/interfaces/protocol.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.interfaces.protocol for the module under test.
"""

from __future__ import annotations

import ipaddress

from hivemind.entrance.expose.interfaces import unscoped


def test_unscoped_drops_an_ipv6_zone() -> None:
    zoned = ipaddress.ip_address("fe80::1%eth0")

    assert unscoped(zoned) == ipaddress.ip_address("fe80::1")
    assert zoned != ipaddress.ip_address("fe80::1")  # Why the rule exists at all.


def test_unscoped_keeps_every_other_address_as_it_is() -> None:
    for text in ("100.101.102.103", "fd7a:115c:a1e0::1", "::1"):
        address = ipaddress.ip_address(text)

        assert unscoped(address) is address
