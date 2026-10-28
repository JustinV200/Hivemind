"""Obey this node's own trail at start: refuse, hold, or run (ADR-0065: never two Queens).

Every start reads the newest Supersedure on this node's trail before the Queen runs. An old Queen
whose Supersedure has no outcome, one that was superseded, and a candidate that was rolled back
never start (`SupersedurePendingError`, one line for the operator). A candidate with no outcome
yet is held: `CandidateHold` keeps her Requeening from settling, and her tick from placing
anything, until the old Queen acknowledges her or the grace window is over with the old Queen
unreachable, which `TcpOldQueen` answers by dialling the old Queen's Waggle address.
`HubNoticeSender` sends a `QueenMoved` to every device connected to the swarm listener, each
signed with the Hive key by the link it goes over.

Fits into the Hive:
    Layer 7 (edges), inside `hivemind.cli.compose.hive`. Called by `.run` (`arm_supersedure`
    before Requeening). Calls into `hivemind.queen.supersedure`, `hivemind.swarm` and waggle only.

Key invariants:
    - Runs under the Queen lock and before Requeening's first half.
    - A probe that cannot connect within `PROBE_TIMEOUT_S` says the old Queen is unreachable.

See Also:
    - docs/runbooks/supersedure.md for what the operator sees when a start is refused.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlsplit

from hivemind.queen.deps import QueenDeps
from hivemind.queen.supersedure import CandidateHold, Recorder, check_start
from hivemind.swarm import DeviceTimeoutError, GatewayHub, GatewayUnavailableError
from waggle.messages.control import QueenMoved

PROBE_TIMEOUT_S = 3.0  # One TCP connect; an answering Queen accepts at once.
_DEFAULT_PORTS = {"ws": 80, "wss": 443}

__all__ = ["PROBE_TIMEOUT_S", "HubNoticeSender", "TcpOldQueen", "arm_supersedure"]


class TcpOldQueen:
    """Whether the old Queen's Waggle address still accepts a connection."""

    def __init__(self, address: str) -> None:
        """Probe `address` (a ws:// or wss:// URI); an empty one is never reachable."""
        parts = urlsplit(address)
        self._host = parts.hostname
        self._port = parts.port or _DEFAULT_PORTS.get(parts.scheme, 0)

    async def reachable(self) -> bool:
        """Open and close one TCP connection; False on refusal or timeout."""
        if not self._host or not self._port:
            return False
        try:
            # External await: one TCP handshake, bounded by PROBE_TIMEOUT_S.
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), PROBE_TIMEOUT_S
            )
        except (OSError, TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return True


class HubNoticeSender:
    """Every device link the swarm listener holds, as Supersedure's `NoticeSender`."""

    def __init__(self, hub: GatewayHub, sender: str) -> None:
        """Send over `hub`'s links as `sender` (the Queen's hive id)."""
        self._hub = hub
        self._sender = sender

    async def broadcast(self, notice: QueenMoved) -> int:
        """Send `notice` to every connected device; one that fails is skipped (it redials)."""
        sent = 0
        for _node, link in self._hub.entries():
            try:
                await link.send(notice, sender=self._sender)
            except (GatewayUnavailableError, DeviceTimeoutError):
                continue  # Its link is failing; the grace window and its redial cover it.
            sent += 1
        return sent


async def arm_supersedure(deps: QueenDeps) -> None:
    """Refuse a start the trail forbids, or hold a candidate (module docstring).

    Args:
        deps: The Queen's collaborators, before her first tick.

    Raises:
        SupersedurePendingError: This node's trail forbids its Queen to start.
    """
    identity = deps.identity
    held = await check_start(deps.trail, identity.node_id)
    if held is None:
        return
    recorder = Recorder(deps.trail, identity.hive_id, identity.node_id, deps.clock)
    deps.requeening.hold = CandidateHold(held, recorder, TcpOldQueen(held.old_address))
