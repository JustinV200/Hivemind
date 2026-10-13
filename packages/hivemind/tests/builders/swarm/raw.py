"""Provide RawLink: a Hive-side link whose device end the test drives by hand, frame by frame.

The scripted device answers like a conforming packet, which is what most tests want. The link,
exchange and hub tests instead need to send exactly the frame they are testing (an unsolicited
event, a reply to an id nobody asked for, a `control.error`, a frame from the wrong node) and to
read exactly what the Hive sent. `RawLink` is the same chain as `LinkRig` without the device: a
started `DeviceLink` over a `MemoryTransport` pair, with the device's end exposed as `send` and
`next_sent`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the gateway tests.

Key invariants:
    - `close` always ends the link, whatever a test did.
    - Frames the test sends are addressed from the device to the Queen and carry the device's
      node id unless a test says otherwise.

See Also:
    - builders.swarm.rig for the same chain with a scripted device.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from builders.swarm.parties import (
    DeviceParty,
    HiveParty,
    make_device_party,
    make_heartbeat,
    make_hive_party,
)

from hivemind.swarm.gateway import DeviceLink, HiveVoice, LinkEnds
from hivemind.swarm.gateway.frames import Connection, open_connection
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import MessageId, NodeId
from waggle.messages.base import WaggleMessage
from waggle.transport.memory import MemoryTransport

__all__ = ["RawLink", "make_raw_link"]

RECEIVE_TIMEOUT_S = 5.0  # A test that waits longer than this has a bug, not a slow machine.


@dataclass
class RawLink:
    """A started link whose device end a test drives by hand."""

    clock: FakeClock
    hive: HiveParty
    party: DeviceParty
    link: DeviceLink
    connection: Connection
    device_end: MemoryTransport
    unawaited: list[Envelope] = field(default_factory=list)
    heard: asyncio.Event = field(default_factory=asyncio.Event)
    _incoming: AsyncGenerator[Envelope, None] | None = None

    async def send(
        self,
        payload: WaggleMessage,
        correlation_id: MessageId | None = None,
        *,
        node_id: NodeId | None = None,
    ) -> Envelope:
        """Send a frame from the device to the Hive; return the envelope that was sent."""
        hop = Hop(
            sender=self.party.device_id,
            recipient=self.hive.hive_id,
            node_id=node_id if node_id is not None else self.party.node_id,
        )
        envelope = wrap(payload, hop, clock=self.clock, correlation_id=correlation_id)
        await self.device_end.send(envelope)
        return envelope

    async def send_heartbeat(self) -> Envelope:
        """Send the device's heartbeat, as it does first on every connection."""
        return await self.send(make_heartbeat(self.party))

    async def next_sent(self) -> Envelope:
        """Wait for the next frame the Hive sent to the device."""
        if self._incoming is None:
            self._incoming = self.device_end.receive()
        async with asyncio.timeout(RECEIVE_TIMEOUT_S):
            return await anext(self._incoming)

    async def close(self) -> None:
        """End the link; safe to call twice."""
        await self.link.close()


async def make_raw_link() -> RawLink:
    """Build and start a link to a bare device end.

    Returns:
        The running link; the caller awaits `close()`.
    """
    clock = FakeClock()
    hive = make_hive_party(clock)
    party = make_device_party(clock)
    hive_end, device_end = MemoryTransport.pair(Codec(), Codec())
    voice = HiveVoice(hive.hive_id, hive.node_id)
    connection = open_connection(hive_end, voice, clock)
    unawaited: list[Envelope] = []
    heard = asyncio.Event()

    async def keep(envelope: Envelope) -> None:
        """Keep a frame no exchange was waiting for, and signal it."""
        unawaited.append(envelope)
        heard.set()

    link = DeviceLink(connection, LinkEnds(voice, party.device_id), clock, keep)
    link.start()
    return RawLink(clock, hive, party, link, connection, device_end, unawaited, heard)
