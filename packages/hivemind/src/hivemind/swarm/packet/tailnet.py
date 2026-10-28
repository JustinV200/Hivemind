"""Define TailnetKeys: the seam a Pollen Packet's single-use tailnet key is minted and revoked by.

A packet puts a new device on the Hive's private network (the tailnet, Tailscale's overlay) before
it enrols, so it carries an auth key that `tailscale up` spends once. The key's terms are fixed
here, not left to a caller: single use, preauthorized (no admin click), not ephemeral (the device
stays on the tailnet when it is offline, so it reconnects by itself from any network) and tagged
`tag:hive-device`, the tag the tailnet policy lets reach the Hive Stand and nothing else. The real
minter is `hivemind.swarm.packet.tailscale.TailscaleApiKeys`; the fake beside it records calls.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Implemented by `tailscale` and `fake`; called by `hivemind.swarm.packet.builder`.

Key invariants:
    - A key is never reusable, always preauthorized, never ephemeral and always tagged
      `HIVE_DEVICE_TAG`; no implementation takes those as arguments.
    - The key itself is a `SecretStr`; only its id (`key_id`) may be printed or recorded.

See Also:
    - docs/adr/0067-the-pollen-packet-carries-a-tailnet-key-and-an-invite.md for the decision.
    - docs/runbooks/enrolling-a-device.md, "Pollen Packet over Tailscale", for the tailnet setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from pydantic import SecretStr

HIVE_DEVICE_TAG = "tag:hive-device"  # The tag the tailnet policy grants the Hive Stand, only.

__all__ = ["HIVE_DEVICE_TAG", "TailnetKey", "TailnetKeys"]


@dataclass(frozen=True, slots=True)
class TailnetKey:
    """One minted auth key: its id, safe to show, and the key, never shown."""

    key_id: str  # Tailscale's id for the key; what the trail records and a revoke names.
    key: SecretStr  # The `tskey-auth-...` string `tailscale up` spends.


class TailnetKeys(Protocol):
    """Mint and revoke single-use, preauthorized, tagged tailnet auth keys."""

    async def mint(self, ttl: timedelta) -> TailnetKey:
        """Mint one key that lapses after `ttl`.

        Args:
            ttl: How long the key stays usable; the invite's own lifetime, so neither outlives
                the other by much. Must be at least one second.

        Returns:
            The key and its id.

        Raises:
            PacketError: The tailnet refused, could not be reached, or the credentials failed.
        """
        ...

    async def revoke(self, key_id: str) -> None:
        """Revoke a key minted for a packet that was not built.

        Args:
            key_id: The id `mint` returned.

        Raises:
            PacketError: The tailnet refused or could not be reached.
        """
        ...
