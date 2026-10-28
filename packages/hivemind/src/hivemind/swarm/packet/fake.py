"""Provide FakeTailnetKeys: an in-memory TailnetKeys for tests and demos, which never dials out.

`hive swarm packet` must be testable with no tailnet and no OAuth client, and must be shown to
leave nothing live when a step fails. The fake mints keys with predictable ids, remembers which
are still live, and can be told to refuse the next mint, which is how a test proves that a failed
key mint leaves no invite behind.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Implements `hivemind.swarm.packet.tailnet.TailnetKeys`; used by tests and demo paths.

Key invariants:
    - `live` holds exactly the ids minted and not yet revoked.
    - A refused mint mints nothing.

See Also:
    - hivemind.swarm.packet.tailnet for the protocol.
    - hivemind.swarm.packet.tailscale for the real implementation.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import SecretStr

from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet.tailnet import TailnetKey

FAKE_KEY_PREFIX = "tskey-auth-fake-"  # Recognisable in a test that hunts for a leaked key.

__all__ = ["FAKE_KEY_PREFIX", "FakeTailnetKeys"]


class FakeTailnetKeys:
    """Mint keys in memory, optionally refusing; record what is live and what each lived for."""

    def __init__(self, *, refuse: bool = False) -> None:
        """Build the fake.

        Args:
            refuse: When true, every mint raises PacketError as an unreachable tailnet would.
        """
        self.refuse = refuse
        self.live: dict[str, TailnetKey] = {}
        self.ttls: list[timedelta] = []

    async def mint(self, ttl: timedelta) -> TailnetKey:
        """Mint one key, or refuse.

        Args:
            ttl: The key's lifetime, recorded in `ttls`.

        Returns:
            A key whose secret starts with `FAKE_KEY_PREFIX`.

        Raises:
            PacketError: `refuse` is set.
        """
        if self.refuse:
            raise PacketError("Tailscale refused to mint a tailnet key: HTTP 403.")
        key_id = f"kfake{len(self.ttls)}"
        self.ttls.append(ttl)
        key = TailnetKey(key_id=key_id, key=SecretStr(f"{FAKE_KEY_PREFIX}{key_id}-secret"))
        self.live[key_id] = key
        return key

    async def revoke(self, key_id: str) -> None:
        """Forget a live key; revoking one already gone is a no-op, as the API's 404 would be.

        Args:
            key_id: The id `mint` returned.
        """
        self.live.pop(key_id, None)
