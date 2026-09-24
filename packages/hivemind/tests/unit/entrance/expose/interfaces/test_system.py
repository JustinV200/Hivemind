"""Tests for hivemind.entrance.expose.interfaces.system: psutil's answer, converted.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/interfaces/system.py (codingrules section 3). The
    conversion is fed records by hand; one test asks the real kernel of whatever host runs it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.interfaces.system for the module under test.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import NamedTuple

from hivemind.entrance.expose.interfaces import SystemInterfaces, interfaces_from_records

_LINK_FAMILY = -1  # psutil's AF_LINK stand-in on Windows; any non-IP family behaves the same.


class _Record(NamedTuple):
    """The two fields of psutil's snicaddr the adapter reads."""

    family: int
    address: str


def test_interfaces_from_records_keeps_ip_addresses_only() -> None:
    records = {
        "tailscale0": [
            _Record(socket.AF_INET, "100.101.102.103"),
            _Record(socket.AF_INET6, "fd7a:115c:a1e0::1"),
            _Record(socket.AF_INET6, "fe80::5%tailscale0"),
        ],
        "eth0": [_Record(_LINK_FAMILY, "02:fc:00:00:00:01")],
    }

    snapshot = interfaces_from_records(records)

    assert [entry.name for entry in snapshot] == ["tailscale0", "eth0"]
    assert snapshot[0].addresses == frozenset(
        ipaddress.ip_address(text) for text in ("100.101.102.103", "fd7a:115c:a1e0::1", "fe80::5")
    )
    assert snapshot[1].addresses == frozenset()


def test_interfaces_from_records_skips_an_address_it_cannot_parse() -> None:
    snapshot = interfaces_from_records({"odd0": [_Record(socket.AF_INET, "not an address")]})

    assert snapshot[0].addresses == frozenset()


async def test_system_interfaces_sees_this_hosts_loopback() -> None:
    snapshot = await SystemInterfaces().snapshot()

    assert any(address.is_loopback for entry in snapshot for address in entry.addresses)
