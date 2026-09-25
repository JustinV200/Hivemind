"""Contract tests for LocalInterfaces, run against the psutil adapter and the fake alike.

Fits into the Hive:
    Codingrules 14.3's contract suite for ``hivemind.entrance.expose.interfaces.LocalInterfaces``,
    parametrised over every implementation. It lives beside the unit tests because this step's
    paths end at tests/unit/entrance/expose; tests/contracts/ is its natural home.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.interfaces.protocol for the protocol under test.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from hivemind.entrance.expose.interfaces import (
    FakeInterfaces,
    InterfaceAddresses,
    LocalInterfaces,
    SystemInterfaces,
)


def _fake() -> LocalInterfaces:
    """A fake host with a loopback, a LAN and an overlay interface, one of them zoned."""
    return FakeInterfaces(
        {
            "lo": ["127.0.0.1", "::1"],
            "eth0": ["192.168.1.20", "fe80::1%eth0"],
            "tailscale0": ["100.101.102.103"],
        }
    )


_IMPLEMENTATIONS: list[Callable[[], LocalInterfaces]] = [_fake, SystemInterfaces]


@pytest.fixture(params=_IMPLEMENTATIONS, ids=["fake", "system"])
def interfaces(request: pytest.FixtureRequest) -> LocalInterfaces:
    """Each implementation in turn."""
    factory: Callable[[], LocalInterfaces] = request.param
    return factory()


async def test_snapshot_lists_each_interface_once(interfaces: LocalInterfaces) -> None:
    snapshot = await interfaces.snapshot()

    names = [entry.name for entry in snapshot]
    assert all(isinstance(entry, InterfaceAddresses) for entry in snapshot)
    assert len(names) == len(set(names))


async def test_snapshot_never_holds_a_zoned_address(interfaces: LocalInterfaces) -> None:
    snapshot = await interfaces.snapshot()

    for entry in snapshot:
        for address in entry.addresses:
            assert getattr(address, "scope_id", None) is None


async def test_snapshot_includes_a_loopback_address(interfaces: LocalInterfaces) -> None:
    snapshot = await interfaces.snapshot()

    assert any(address.is_loopback for entry in snapshot for address in entry.addresses)
