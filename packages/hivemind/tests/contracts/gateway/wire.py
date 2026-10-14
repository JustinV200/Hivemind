"""Stand in for the Hive Stand byte for byte, so a gateway can be shown malformed and forged frames.

The real swarm listener (`builders.swarm.live.LiveHive`) signs everything it sends correctly and
hides the socket, which is what most of the conformance suite wants. A few clauses are about the
bytes themselves: a replayed envelope is the same bytes twice, a tampered frame is a signed frame
with one character changed, a gateway closes with a particular WebSocket code. `RawHive` serves
those. It stops the real listener, binds the same address with a bare `websockets` server so that
the gateway (which keeps dialling the address it enrolled with) connects to it, and then reads
and writes whole frames: `signed` builds a frame the way the Hive Stand's codec does (so a good
frame is exactly a good frame), `send` puts any bytes or text on the wire, `next_envelope`
decodes what the gateway sent with the gateway's own public key, and `closed_code` reports the
WebSocket close code the gateway ended the connection with. `give_back` restarts the real
listener on the same port. A Hive Stand that serves `wss://` is replaced by a bare server that
serves it with the same certificate, so a gateway that pins it (one in a container, say) dials the
stand-in exactly as it dialled the listener.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the resilience part of
    the gateway conformance suite; builds on `builders.swarm.live` for the Hive Stand it
    replaces and on waggle's codec for the frames.

Key invariants:
    - A frame built by `signed` is exactly what the real listener would have sent for the same
      payload, signed with the Hive Stand's real key, unless a test asks for something else.
    - Every wait is bounded; a gateway that never dials in is a failed test, not a hang.
    - `stop` leaves the address free, so the real listener can come back on it.

See Also:
    - docs/waggle/gateway-contract.md sections 5 to 7 for the clauses this exercises.
    - waggle.codec for the frame format and the checks a receiver applies.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime

from builders.swarm.live import LiveHive
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from hivemind.swarm.models import SwarmNode
from waggle.clock import SystemClock
from waggle.codec import Codec, Signer
from waggle.envelope import Envelope
from waggle.ids import DeviceId, MessageId, new_message_id
from waggle.messages.base import WaggleMessage
from waggle.messages.registry import kind_for
from waggle.signing import Ed25519Verifier
from waggle.transport.tls import server_ssl_context

DIAL_TIMEOUT_S = 30.0  # How long the gateway may take to dial the address after the swap.
FRAME_TIMEOUT_S = 15.0  # How long a frame the gateway owes may take to arrive.
CLOSE_TIMEOUT_S = 15.0  # How long the gateway may take to close a connection it rejects.

__all__ = ["Frame", "RawHive"]


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame as it goes on the wire: its envelope id and its exact bytes."""

    id: MessageId
    data: bytes


class RawHive:
    """A bare WebSocket server standing where the Hive Stand's listener stood."""

    def __init__(self, hive: LiveHive, node: SwarmNode) -> None:
        """Prepare to replace `hive`'s listener, for the gateway enrolled as `node`.

        Args:
            hive: The running Hive Stand whose listener is replaced.
            node: The gateway's node, whose public key verifies what it sends.
        """
        self.hive = hive
        self.node = node
        self._clock = SystemClock()
        verifier = Ed25519Verifier({str(node.node_id): bytes.fromhex(node.public_key_hex)})
        self._codec = Codec(signer=hive.signer, verifier=verifier)
        self._accepted: asyncio.Queue[ServerConnection] = asyncio.Queue()
        self._server: Server | None = None
        self._drainer: asyncio.Task[None] | None = None
        self.connection: ServerConnection | None = None

    async def take_over(self) -> ServerConnection:
        """Stop the real listener, serve its address by hand and wait for the gateway's dial."""
        await self.hive.stop()
        config = self.hive.config
        # The listener's own certificate and key, so a gateway pinning the certificate still
        # trusts what it dials; plaintext only where the real listener was.
        tls = (
            server_ssl_context(config.tls_cert, config.tls_key)
            if config.tls_cert is not None and config.tls_key is not None
            else None
        )
        self._server = await serve(self._accept, config.host, config.port, ssl=tls)
        return await self.next_connection()

    async def next_connection(self) -> ServerConnection:
        """Wait for the gateway to dial in (again) and make that connection the current one."""
        async with asyncio.timeout(DIAL_TIMEOUT_S):  # The gateway re-dials with its own backoff.
            self.connection = await self._accepted.get()
        return self.connection

    async def give_back(self) -> None:
        """Close the hand-made server and start the real listener again on the same port."""
        await self.stop()
        await self.hive.restart()

    def drain(self) -> None:
        """Read and discard whatever the gateway sends from now on, so that it can close politely.

        A connection nobody reads from stops reading once its queue is full, and a close
        handshake with it then waits out its timeout; draining is how a test that has finished
        with the gateway lets it shut down at once.
        """
        if self.connection is not None and self._drainer is None:
            self._drainer = asyncio.ensure_future(_discard(self.connection))

    async def stop(self) -> None:
        """Close the server and every connection to it; safe to call twice."""
        if self._drainer is not None:
            self._drainer.cancel()
            await asyncio.gather(self._drainer, return_exceptions=True)
            self._drainer = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def signed(
        self,
        payload: WaggleMessage,
        *,
        recipient: DeviceId | None = None,
        sent_at: datetime | None = None,
    ) -> Frame:
        """Build the frame the Hive Stand would send for `payload`, signed with its real key.

        Args:
            payload: The message to carry.
            recipient: Who it is addressed to; the gateway's device by default.
            sent_at: The time it claims; now by default.
        """
        return self._encode(self._codec, payload, recipient, sent_at)

    def unsigned(self, payload: WaggleMessage) -> Frame:
        """Build a frame that carries no signature at all."""
        return self._encode(Codec(), payload, None, None)

    def forged(self, payload: WaggleMessage, signer: Signer) -> Frame:
        """Build a frame in the Hive Stand's name that `signer` signed instead of its key."""
        return self._encode(Codec(signer=signer), payload, None, None)

    def _encode(
        self,
        codec: Codec,
        payload: WaggleMessage,
        recipient: DeviceId | None,
        sent_at: datetime | None,
    ) -> Frame:
        """Wrap `payload` as a frame from the Hive Stand and encode it with `codec`."""
        voice = self.hive.party
        message_id = new_message_id(self._clock)
        envelope = Envelope(
            id=message_id,
            correlation_id=None,
            sender=voice.hive_id,
            recipient=recipient or self.node.device_id,
            kind=kind_for(type(payload)),
            sent_at=sent_at or self._clock.now(),
            node_id=voice.node_id,
            payload=payload,
        )
        return Frame(message_id, codec.encode(envelope))

    async def send(self, data: bytes | str) -> None:
        """Put bytes (a binary frame) or text (a text frame) on the current connection."""
        assert self.connection is not None, "the gateway has not dialled in"
        await self.connection.send(data)

    async def next_envelope(self) -> Envelope:
        """Receive one frame from the gateway and decode it, verifying its signature."""
        assert self.connection is not None, "the gateway has not dialled in"
        async with asyncio.timeout(FRAME_TIMEOUT_S):
            data = await self.connection.recv()
        assert isinstance(data, bytes), "the gateway sent a text frame"
        return self._codec.decode(data)

    async def until_answered(self, marker: MessageId) -> list[Envelope]:
        """Collect what the gateway sends up to and including the answer to `marker`.

        Frames on one connection are handled in order, so everything the gateway owed for frames
        sent before `marker` is in the list when it ends. Heartbeats and reports are included;
        the caller picks out what it is looking for.
        """
        seen: list[Envelope] = []
        while True:
            envelope = await self.next_envelope()
            seen.append(envelope)
            if envelope.correlation_id == marker:
                return seen

    async def closed_code(self) -> int | None:
        """Wait for the gateway to close the current connection and return its close code."""
        assert self.connection is not None, "the gateway has not dialled in"
        async with asyncio.timeout(CLOSE_TIMEOUT_S):
            await self.connection.wait_closed()
        return self.connection.close_code

    async def _accept(self, connection: ServerConnection) -> None:
        """Hand a new connection to whoever is waiting for the gateway, and hold it open."""
        await self._accepted.put(connection)
        await connection.wait_closed()


async def _discard(connection: ServerConnection) -> None:
    """Receive and drop every message of a connection until it closes."""
    with contextlib.suppress(ConnectionClosed):
        async for _message in connection:
            continue
