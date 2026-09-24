"""Provide FakePeerEndpointSource: a PeerEndpointSource whose peers a test places by hand.

Codingrules 14.4 keeps fakes beside their protocol, honest and production quality. This one answers
from a mapping of overlay address to current network that a test (or a demo) moves with
``place``, so the travel lock can be driven through a device staying home, roaming to a new network
and coming back, without tailscaled. Networks are checked like the real source's output, so a test
cannot place a peer on a network the real source would never report.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.travel``. Used by
    the travel-lock and login tests; never by a production composition root. Calls into
    ``hivemind.entrance.auth.network`` only.

Key invariants:
    - Answers exactly what was last placed for an address, None for an address never placed.

See Also:
    - hivemind.entrance.auth.travel.source for PeerEndpointSource.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.entrance.auth.network import is_device_network

__all__ = ["FakePeerEndpointSource"]


class FakePeerEndpointSource:
    """A PeerEndpointSource over a mapping of overlay address to current network."""

    def __init__(self, networks: Mapping[str, str | None] | None = None) -> None:
        """Start with ``networks`` placed.

        Args:
            networks: Overlay address to current network (None: the source cannot place that
                peer); None for no peer placed yet.

        Raises:
            ValueError: A network is not a canonical device network.
        """
        self._networks: dict[str, str | None] = {}
        self.asked: list[str] = []
        for address, network in (networks or {}).items():
            self.place(address, network)

    def place(self, address: str, network: str | None) -> None:
        """Say the peer at ``address`` now connects from ``network`` (None: it cannot be placed).

        Args:
            address: The peer's overlay address.
            network: Its current network, or None for "the source cannot tell".

        Raises:
            ValueError: ``network`` is not a canonical device network.
        """
        if network is not None and not is_device_network(network):
            raise ValueError("A placed network is a canonical /24, /64 or derp:<region>.")
        self._networks[address] = network

    async def current_network(self, address: str) -> str | None:
        """Return what was last placed for ``address``; see PeerEndpointSource.current_network."""
        self.asked.append(address)
        return self._networks.get(address)
