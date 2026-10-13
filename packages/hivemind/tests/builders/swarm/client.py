"""Drive the swarm listener from the device's side over a real socket, frame by frame.

The router tests send frames over in-memory connections; the listener tests must prove the same
behaviour across a real TCP (and TLS) connection, where the transport's own limits, the signature
policy and the close codes are the real ones. `DeviceClient` is a device's Pollen Packet reduced to
its transport: it dials the listener with the device's real signing key, verifies what the Hive
sends against the Hive Stand's key (so a reply signed by anyone else fails), and exposes `send`,
`next` and `closed` so a test says exactly which frame goes out and what must come back.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the listener tests under
    tests/unit/swarm/gateway.

Key invariants:
    - The device end verifies every frame it receives with the Hive Stand's public key.
    - `close` is safe to call twice and after the Hive ended the connection.

See Also:
    - builders.swarm.dial for the same frames over in-memory connections.
    - hivemind.swarm.gateway.listener for the listener this client dials.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from builders.swarm.enrolment import make_enrol_envelope, make_enrol_request
from builders.swarm.hive import HiveRig
from builders.swarm.parties import DeviceParty, make_heartbeat

from waggle.codec import Codec, Signer
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.ids import MessageId, NodeId
from waggle.messages.base import WaggleMessage
from waggle.signing import Ed25519Verifier
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport

__all__ = ["DeviceClient"]

WAIT_S = 5.0  # A test that waits longer than this has a bug, not a slow machine.


@dataclass
class DeviceClient:
    """A device dialling the listener at `uri` with its own signing key."""

    stand: HiveRig
    party: DeviceParty
    uri: str
    pin: str | None = None
    signer: Signer | None = None
    transport: WebSocketClientTransport = field(init=False)
    _incoming: AsyncGenerator[Envelope, None] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Build the transport: signing as the device, verifying only the Hive Stand's key."""
        verifier = Ed25519Verifier(
            {self.stand.hive.node_id: self.stand.hive.signer.public_key_bytes}
        )
        codec = Codec(
            signer=self.signer if self.signer is not None else self.party.signer, verifier=verifier
        )
        # One attempt: a refused connection is the result under test, not something to retry.
        options = DialOptions(max_attempts=1, tls_pin_sha256=self.pin)
        self.transport = WebSocketClientTransport(
            self.uri, codec, self.stand.clock, options=options
        )

    async def connect(self) -> None:
        """Dial the listener."""
        await self.transport.connect()

    def envelope(
        self,
        payload: WaggleMessage,
        correlation_id: MessageId | None = None,
        *,
        sender: str | None = None,
        node_id: NodeId | None = None,
    ) -> Envelope:
        """Wrap `payload` as a frame from the device to the Queen, with a fresh id."""
        hop = Hop(
            sender=sender if sender is not None else self.party.device_id,
            recipient=self.stand.hive.hive_id,
            node_id=node_id if node_id is not None else self.party.node_id,
        )
        return wrap(payload, hop, clock=self.stand.clock, correlation_id=correlation_id)

    async def send(
        self,
        payload: WaggleMessage,
        correlation_id: MessageId | None = None,
        *,
        sender: str | None = None,
        node_id: NodeId | None = None,
    ) -> Envelope:
        """Send `payload` as a new frame; return the envelope that was sent."""
        sent = self.envelope(payload, correlation_id, sender=sender, node_id=node_id)
        await self.transport.send(sent)
        return sent

    async def send_heartbeat(self, **overrides: object) -> Envelope:
        """Send the device's heartbeat, the first frame of every connection after enrolment."""
        return await self.send(make_heartbeat(self.party, **overrides))

    async def send_enrol(self, code: str, **overrides: object) -> Envelope:
        """Send a signed `swarm.enrol_request` carrying `code`; return the envelope sent."""
        request = make_enrol_request(self.party, code, **overrides)
        sent = make_enrol_envelope(self.party, self.stand.hive, request, self.stand.clock)
        await self.transport.send(sent)
        return sent

    async def next(self) -> Envelope:
        """Wait for the next frame the Hive sends."""
        if self._incoming is None:
            self._incoming = self.transport.receive()
        async with asyncio.timeout(WAIT_S):
            return await anext(self._incoming)

    async def closed(self) -> bool:
        """Read until the Hive ends the connection; True when it did, False if it stayed open."""
        if self._incoming is None:
            self._incoming = self.transport.receive()
        try:
            async with asyncio.timeout(WAIT_S):
                async for _frame in self._incoming:
                    continue
        except (ConnectionLostError, TransportClosedError):
            return True
        except TimeoutError:
            return False
        return True

    async def close(self) -> None:
        """End the connection from the device's side; safe after the Hive ended it."""
        await self.transport.close()
