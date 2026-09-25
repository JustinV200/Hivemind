"""Define PeerEndpointSource: who can tell the travel lock where a device really connects from.

On the Tailscale overlay a device keeps its overlay address wherever it roams, so the address a
request arrives from says nothing about where the device is (ADR-0041). The travel lock therefore
asks something that sees real endpoints: for the overlay address a request came from, the network
the peer is reaching the Hive Stand from right now (its current endpoint's /24 or /64, or
``derp:<region>`` when tailscaled relays it). ``PeerEndpointSource`` is that seam (codingrules
8.1), implemented by ``TailscaleEndpointSource`` (tailscaled's local API) and
``FakePeerEndpointSource`` (tests and demos).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.travel``. Called by
    ``TravelLock`` for every remote login while ``travel_lock`` is on. Implementations call out to
    tailscaled (or nothing).

Key invariants:
    - ``current_network`` returns a canonical device network (``hivemind.entrance.auth.network``)
      or None; it never raises for a peer it cannot place, since "unknown" is an answer the
      travel lock treats as a new network.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "travel lock".
    - hivemind.entrance.auth.travel.tailscale for the real source.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["PeerEndpointSource"]


class PeerEndpointSource(Protocol):
    """Say which network the peer behind an overlay address is connecting from right now."""

    async def current_network(self, address: str) -> str | None:
        """Return the network the peer at ``address`` currently reaches the Hive Stand from.

        Args:
            address: The overlay address a request arrived from.

        Returns:
            The peer's current network (a /24, a /64 or ``derp:<region>``), or None when the
            source cannot place it (no such peer, no endpoint, the source unreachable).
        """
        ...
