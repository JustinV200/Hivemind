"""Tests for hivemind.entrance.expose.interfaces.fake: a host built from a table.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/interfaces/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.interfaces.fake for the module under test.
"""

from __future__ import annotations

import ipaddress

import pytest

from hivemind.entrance.expose.interfaces import FakeInterfaces, InterfaceAddresses


async def test_fake_interfaces_answers_its_table_with_zones_dropped() -> None:
    fake = FakeInterfaces({"eth0": ["192.168.1.20", "fe80::1%eth0"], "tun0": []})

    snapshot = await fake.snapshot()

    assert snapshot == (
        InterfaceAddresses(
            name="eth0",
            addresses=frozenset(
                {ipaddress.ip_address("192.168.1.20"), ipaddress.ip_address("fe80::1")}
            ),
        ),
        InterfaceAddresses(name="tun0", addresses=frozenset()),
    )
    assert await fake.snapshot() == snapshot


def test_fake_interfaces_refuses_a_malformed_address_where_it_is_written() -> None:
    with pytest.raises(ValueError, match=r"192\.168\.1\.300"):
        FakeInterfaces({"eth0": ["192.168.1.300"]})
