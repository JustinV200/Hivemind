"""Provide FakeInterfaces, a LocalInterfaces answering from a table instead of the operating system.

The exposure check needs a host's interfaces to decide ``vpn`` and ``lan`` (ADR-0041), and a test,
a demo or ``hive doctor`` needs to put any host in front of it: a laptop on Tailscale, a server with
carrier-grade NAT on its WAN port, a Mac whose overlay is ``utun4``. ``FakeInterfaces`` is that
host, built from a mapping of interface names to address strings and parsed exactly as the real
adapter parses the operating system's answer (zones dropped), so the two are interchangeable
(codingrules 14.4: fakes live beside their protocol and are production quality).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.interfaces``.
    Implements ``LocalInterfaces``; used by tests and anything that plans an exposure for a host
    other than this one. Imports the standard library and the protocol only.

Key invariants:
    - ``snapshot`` returns the table it was built with, every call, in the order given.
    - A malformed address is refused when the fake is built, never silently dropped.

See Also:
    - hivemind.entrance.expose.interfaces.protocol for the protocol and the snapshot value.
    - hivemind.entrance.expose.interfaces.system for the real adapter.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping

from hivemind.entrance.expose.interfaces.protocol import InterfaceAddresses, unscoped

__all__ = ["FakeInterfaces"]


class FakeInterfaces:
    """A LocalInterfaces over a fixed table: interface name to the addresses assigned to it."""

    def __init__(self, table: Mapping[str, Iterable[str]]) -> None:
        """Build the fake host.

        Args:
            table: Each interface's name and its addresses as text, e.g.
                ``{"tailscale0": ["100.101.102.103", "fd7a:115c:a1e0::1"], "lo": ["127.0.0.1"]}``;
                an IPv6 zone (``fe80::1%eth0``) is accepted and dropped.

        Raises:
            ValueError: An address is not an IPv4 or IPv6 address.
        """
        # Parsed once, up front: a typo in a test's table fails where it was written.
        self._interfaces = tuple(
            InterfaceAddresses(
                name=name,
                addresses=frozenset(unscoped(ipaddress.ip_address(text)) for text in addresses),
            )
            for name, addresses in table.items()
        )

    async def snapshot(self) -> tuple[InterfaceAddresses, ...]:
        """Return the table this fake was built with.

        Returns:
            One ``InterfaceAddresses`` per table entry, in the table's order.
        """
        return self._interfaces
