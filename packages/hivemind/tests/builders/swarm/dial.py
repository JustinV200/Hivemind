"""Serve a ConnectionRouter over in-memory connections and drive the device end by hand.

The router decides what a connection is from its first frame, so its tests must send exactly the
first frame under test and read exactly what comes back. `RouterRig` is the router with the
collaborators the listener gives it (the key ring, the heartbeat monitor), and `dial` opens one
in-memory connection to it: the Hive's end is served by the router in a task, and the returned
`Dialled` is the device's end, signing with the device party's real key through a codec whose
verifier on the Hive side is the ring, exactly as on the real listener. Nothing here opens a
socket; the same frames over real sockets are tested against `SwarmListener` itself.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the router tests under
    tests/unit/swarm/gateway.

Key invariants:
    - The Hive's end verifies with a `SwarmVerifier` over the rig's `KeyRing`, so a frame from a
      key the ring does not trust never reaches the router.
    - `RouterRig.close` ends every connection it dialled, whatever a test did.

See Also:
    - builders.swarm.hive for the stand the router is built over.
    - hivemind.swarm.gateway.routing for the module these drive.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from builders.swarm.enrolment import enrol_node, make_enrol_envelope, make_enrol_request
from builders.swarm.hive import HiveRig, make_hive_rig
from builders.swarm.parties import (
    DeviceParty,
    make_device_party,
    make_heartbeat,
    make_swarm_node,
)

from hivemind.swarm.gateway import (
    ConnectionRouter,
    HeartbeatMonitor,
    KeyRing,
    LivenessRules,
    RouterDeps,
    SwarmVerifier,
)
from hivemind.swarm.models import SwarmNode
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectionLostError
from waggle.ids import MessageId, NodeId
from waggle.messages.base import WaggleMessage
from waggle.messages.swarm import EnrolRequest
from waggle.transport.memory import MemoryTransport

__all__ = ["Dialled", "RouterRig", "make_router_rig"]

RECEIVE_TIMEOUT_S = 5.0  # A test that waits longer than this has a bug, not a slow machine.


@dataclass
class Dialled:
    """The device's end of one in-memory connection to the router."""

    stand: HiveRig
    party: DeviceParty
    device_end: MemoryTransport
    hive_end: MemoryTransport
    served: asyncio.Task[None]
    _incoming: AsyncGenerator[Envelope, None] | None = field(default=None, repr=False)

    async def send(
        self,
        payload: WaggleMessage,
        correlation_id: MessageId | None = None,
        *,
        sender: str | None = None,
        node_id: NodeId | None = None,
    ) -> Envelope:
        """Send a frame from the device to the Hive, signed with the device's key."""
        hop = Hop(
            sender=sender if sender is not None else self.party.device_id,
            recipient=self.stand.hive.hive_id,
            node_id=node_id if node_id is not None else self.party.node_id,
        )
        envelope = wrap(payload, hop, clock=self.stand.clock, correlation_id=correlation_id)
        await self.device_end.send(envelope)
        return envelope

    async def send_heartbeat(self, **overrides: object) -> Envelope:
        """Send the device's heartbeat, as it does first on every connection."""
        return await self.send(make_heartbeat(self.party, **overrides))

    async def send_enrol(self, token: str, **overrides: object) -> Envelope:
        """Send a signed `swarm.enrol_request` carrying `token`, as a new device's first frame."""
        request: EnrolRequest = make_enrol_request(self.party, token, **overrides)
        envelope = make_enrol_envelope(self.party, self.stand.hive, request, self.stand.clock)
        await self.device_end.send(envelope)
        return envelope

    def send_as(self, other: DeviceParty, payload: WaggleMessage) -> Envelope:
        """Deliver a frame signed by `other`'s key to the Hive's end, as if this peer had sent it.

        The pair's codec signs with one key, so a frame from a second key can only be injected as
        bytes; the Hive's end verifies it like any other.
        """
        hop = Hop(sender=other.device_id, recipient=self.stand.hive.hive_id, node_id=other.node_id)
        envelope = wrap(payload, hop, clock=self.stand.clock)
        self.hive_end.inject_frame(Codec(signer=other.signer).encode(envelope))
        return envelope

    @property
    def close_code(self) -> int | None:
        """The close code the Hive ended the connection with, or None while it is open."""
        return self.device_end.close_code

    async def next_sent(self) -> Envelope:
        """Wait for the next frame the Hive sent to the device."""
        if self._incoming is None:
            self._incoming = self.device_end.receive()
        async with asyncio.timeout(RECEIVE_TIMEOUT_S):
            return await anext(self._incoming)

    async def ended(self) -> bool:
        """Wait for the Hive to end the connection; True when it did without sending more."""
        if self._incoming is None:
            self._incoming = self.device_end.receive()
        try:
            async with asyncio.timeout(RECEIVE_TIMEOUT_S):
                await anext(self._incoming)
        except (StopAsyncIteration, ConnectionLostError):
            return True  # Closed cleanly, or closed with a protocol code after a refused frame.
        return False

    async def finished(self) -> None:
        """Wait for the router to finish serving this connection."""
        async with asyncio.timeout(RECEIVE_TIMEOUT_S):
            await self.served

    async def close(self) -> None:
        """End the connection from the device's side and stop the router's task."""
        await self.device_end.close()
        self.served.cancel()
        await asyncio.gather(self.served, return_exceptions=True)


@dataclass
class RouterRig:
    """A router over a stand's registry, door and hub, with its key ring and monitor."""

    stand: HiveRig
    ring: KeyRing
    monitor: HeartbeatMonitor
    router: ConnectionRouter
    connections: list[Dialled] = field(default_factory=list)

    async def enrolled(
        self, party: DeviceParty | None = None, **overrides: object
    ) -> tuple[DeviceParty, SwarmNode]:
        """Put a device into the registry and trust its key, as enrolment leaves things.

        Args:
            party: The device; a fresh one when omitted.
            **overrides: Registry-row fields that replace the defaults (a status, say).

        Returns:
            The party and its stored node.
        """
        owner = party if party is not None else make_device_party(self.stand.clock)
        node = make_swarm_node(owner, self.stand.clock, **overrides)
        stored = await enrol_node(self.stand.registry, node, self.stand.clock)
        self.ring.add(stored)
        return owner, stored

    def dial(self, party: DeviceParty) -> Dialled:
        """Open an in-memory connection to the router as `party` and start serving it."""
        hive_codec = self.stand.hive.codec(SwarmVerifier(self.ring))
        hive_end, device_end = MemoryTransport.pair(hive_codec, Codec(signer=party.signer))
        served = asyncio.ensure_future(self.router.serve(hive_end))
        dialled = Dialled(self.stand, party, device_end, hive_end, served)
        self.connections.append(dialled)
        return dialled

    async def close(self) -> None:
        """End every connection that was dialled."""
        for dialled in self.connections:
            await dialled.close()


def make_router_rig(stand: HiveRig | None = None, rules: LivenessRules | None = None) -> RouterRig:
    """Build a router over `stand` (a fresh one when omitted), as the listener would.

    Args:
        stand: The Hive Stand pieces to serve; a fresh in-memory stand when omitted.
        rules: Liveness rules for the monitor; four missed beats when omitted.

    Returns:
        The rig; the caller awaits `close()`.
    """
    base = stand if stand is not None else make_hive_rig()
    ring = KeyRing()
    monitor = HeartbeatMonitor(
        base.registry, base.hub, base.recorder, rules if rules is not None else LivenessRules(4)
    )
    deps = RouterDeps(base.registry, base.door, base.hub, monitor, ring, base.voice, base.clock)
    return RouterRig(base, ring, monitor, ConnectionRouter(deps))
