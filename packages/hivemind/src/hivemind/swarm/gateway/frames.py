"""Define Connection and HiveVoice: one accepted connection's frames, read by exactly one reader.

A `Transport` allows one task to receive at a time, and the swarm listener needs two things from
a connection's frames: its first frame, to decide what the connection is for, and every frame
after it, for whatever then owns the connection (a device's link, or a Warden the device runs).
This module makes that a single async iterator that is never abandoned and never read twice:
`open_connection` wraps the transport's `receive()` once, `next_frame` takes the first frame
under a timeout, and the same iterator travels on to the link or the Warden handler. It also
keeps a connection alive across a frame whose payload failed validation: the codec raises
`InvalidPayloadError` for a frame with a readable id, the spec says the receiver answers it with a
`control.error` and the connection stays open (spec section 7), and a `receive()` generator that
raised is finished, so the wrapper answers and starts a fresh `receive()` on the same connection.
`HiveVoice` is the Hive Stand's own address (the Queen's bee address and the Hive Stand's node
id), which every frame the Hive sends on this listener carries.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Built by the listener for each accepted connection; read by the connection router and the
    device link. Calls into waggle (envelope, errors, ids, messages, transport) only.

Key invariants:
    - Exactly one `receive()` generator is live per connection at a time, and the iterator in a
      `Connection` is the only reader: nothing else may call `transport.receive()`.
    - An invalid payload is answered with a `control.error` (when it can be addressed) and does
      not end the connection; every other decode failure does, because the transport closed it.
    - `next_frame` never raises for an ended, refused or silent connection: it returns None.

See Also:
    - docs/waggle/spec.md section 7 for the invalid-payload rule.
    - hivemind.queen.cell_gate.listener for the fan-out this single-reader shape mirrors.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from pydantic import ValidationError

from hivemind.common.logging import get_logger
from hivemind.swarm.errors import DeviceTimeoutError, GatewayUnavailableError
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import (
    CodecError,
    ConnectionLostError,
    FrameCeilingError,
    InvalidPayloadError,
    SignatureError,
    TransportClosedError,
)
from waggle.ids import DeviceId, HiveId, MessageId, NodeId
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.control.protocol import MAX_ERROR_MESSAGE_CHARS
from waggle.messages.registry import kind_for
from waggle.transport.base import Transport

# A control.error is one small frame; a peer that cannot take it in this long is gone.
ERROR_REPLY_TIMEOUT_S = 5.0

# One frame onto a healthy link takes milliseconds; a peer whose socket stopped taking data fails
# here instead of holding the sender for good.
SEND_TIMEOUT_S = 15.0

log = get_logger(__name__)

# Why a connection's frames stop being readable: each of these ends the stream for good, and the
# transport has already closed the connection with the code its error maps to.
ENDED_BY = (
    ConnectionLostError,
    CodecError,
    SignatureError,
    FrameCeilingError,
    TransportClosedError,
)

__all__ = [
    "ENDED_BY",
    "SEND_TIMEOUT_S",
    "Connection",
    "FrameSender",
    "HiveVoice",
    "LinkEnds",
    "next_frame",
    "open_connection",
    "pinned",
]


@dataclass(frozen=True, slots=True)
class HiveVoice:
    """The Hive Stand's own address: who every frame it sends on the swarm listener comes from.

    Attributes:
        hive_id: The Queen's bee address, the `sender` of Queen-kind frames and the recipient of
            everything a device sends up.
        hive_node_id: The Hive Stand's node id, whose key signs every frame the listener sends.
    """

    hive_id: HiveId
    hive_node_id: NodeId


@dataclass(frozen=True, slots=True)
class LinkEnds:
    """The two ends of a link: who the Hive speaks as, and which device it reaches.

    Attributes:
        voice: The Hive Stand's own address: its bee address and the node id that signs.
        device_id: The device's bee address, the recipient of every frame sent on the link.
    """

    voice: HiveVoice
    device_id: DeviceId


@dataclass(frozen=True, slots=True)
class Connection:
    """One accepted connection: the transport to send on and the one iterator to read from.

    Attributes:
        transport: The accepted WebSocket transport; sends and the final close go through it.
        frames: Every frame the peer sends, verified and decoded, ending when the connection does.
            The only reader of `transport.receive()`.
        remote_address: The peer's address as the transport reported it (`host:port`), or None
            when the transport names none; what the enrolment attempt bucket is keyed by.
    """

    transport: Transport
    frames: AsyncGenerator[Envelope, None]
    remote_address: str | None = None


class FrameSender:
    """Send the Hive's frames to one device, one at a time, signed as the Hive Stand."""

    def __init__(self, transport: Transport, ends: LinkEnds, clock: Clock) -> None:
        """Build the sender for one connection.

        Args:
            transport: The accepted transport; its codec signs every frame sent.
            ends: The Hive's address, which every frame carries, and the device's.
            clock: Stamps each envelope's time.
        """
        self._transport = transport
        self._ends = ends
        self._clock = clock
        self._lock = asyncio.Lock()  # A Transport allows one sender at a time.

    async def send(
        self,
        payload: WaggleMessage,
        *,
        sender: str,
        correlation_id: MessageId | None,
        message_id: MessageId,
    ) -> MessageId:
        """Wrap `payload` with the Hive's address and send it under the send lock.

        Args:
            payload: The message to send.
            sender: The bee address it comes from: `hive_id` or the device's `warden_id`.
            correlation_id: What it follows from, when it has an antecedent.
            message_id: The envelope id to send it under.

        Returns:
            `message_id`, once the frame has been taken by the connection.

        Raises:
            GatewayUnavailableError: The connection is closed or lost.
            DeviceTimeoutError: The connection took no frame within `SEND_TIMEOUT_S`.
        """
        envelope = Envelope(
            id=message_id,
            correlation_id=correlation_id,
            sender=sender,
            recipient=self._ends.device_id,
            kind=kind_for(type(payload)),
            sent_at=self._clock.now(),
            node_id=self._ends.voice.hive_node_id,
            payload=payload,
        )
        async with self._lock:
            try:
                # Network write: microseconds to buffer, milliseconds to drain; the bound only
                # catches a peer whose socket stopped taking data before the keepalive notices.
                async with asyncio.timeout(SEND_TIMEOUT_S):
                    await self._transport.send(envelope)
            except (TransportClosedError, ConnectionLostError) as exc:
                raise GatewayUnavailableError(
                    f"The link to device {self._ends.device_id} was lost before frame "
                    f"{message_id} could be sent."
                ) from exc
            except TimeoutError as exc:
                raise DeviceTimeoutError(
                    f"The link to device {self._ends.device_id} took no frame for "
                    f"{SEND_TIMEOUT_S} seconds, so frame {message_id} was not sent."
                ) from exc
        return message_id


def open_connection(
    transport: Transport, voice: HiveVoice, clock: Clock, remote_address: str | None = None
) -> Connection:
    """Wrap an accepted transport so its frames have a single reader that survives bad payloads.

    Args:
        transport: The accepted connection.
        voice: The Hive's address, used to answer a frame that failed validation.
        clock: Mints the id and time of such an answer.
        remote_address: The peer's address as the listener's transport reported it, if any.

    Returns:
        The connection, with its frames iterator not yet started.
    """
    return Connection(
        transport=transport,
        frames=_read_frames(transport, voice, clock),
        remote_address=remote_address,
    )


async def next_frame(connection: Connection, timeout_s: float) -> Envelope | None:
    """Wait for the connection's next frame, or learn that there will not be one.

    Args:
        connection: The connection to read.
        timeout_s: Real seconds to wait for the frame.

    Returns:
        The next verified, decoded envelope; or None when the peer closed, the transport refused
        a frame (and closed the connection), or nothing arrived in time. After a None the
        connection's iterator is finished and the caller closes the transport.
    """
    try:
        # Network read: resolves when the peer sends a frame or the connection ends; the bound is
        # the caller's, because a peer that connects and says nothing must not hold a task forever.
        async with asyncio.timeout(timeout_s):
            return await anext(connection.frames)
    except (StopAsyncIteration, TimeoutError, *ENDED_BY):
        return None


def pinned(connection: Connection, node_id: str) -> Connection:
    """Cut a connection's frames off at the first one signed by a node other than `node_id`.

    Args:
        connection: The connection whose first verified frame came from `node_id`.
        node_id: The node the connection is pinned to for the rest of its life.

    Returns:
        A connection over the same transport whose frames end, as if the peer had closed, the
        moment a frame arrives signed by anyone else. Every key on the ring verifies on this
        listener, so without the pin one enrolled device could speak on another's connection.
    """
    return Connection(
        transport=connection.transport,
        frames=_pinned_frames(connection.frames, node_id),
        remote_address=connection.remote_address,
    )


async def _pinned_frames(
    frames: AsyncGenerator[Envelope, None], node_id: str
) -> AsyncGenerator[Envelope, None]:
    """Yield `frames` until one is signed by a node other than `node_id`, then end."""
    try:
        async for envelope in frames:
            if envelope.node_id != node_id:
                # Only ids are logged: the frame itself may be hostile and is never echoed.
                log.warning("swarm.node_pin_violated", pinned=node_id, got=envelope.node_id)
                return
            yield envelope
    finally:
        # The inner reader is finished or suspended at its yield; closing it here keeps a
        # discarded connection from leaving a half-read generator for the garbage collector.
        await frames.aclose()


async def _read_frames(
    transport: Transport, voice: HiveVoice, clock: Clock
) -> AsyncGenerator[Envelope, None]:
    """Yield every frame of one connection, answering an invalid payload and carrying on."""
    while True:
        try:
            async for envelope in transport.receive():
                yield envelope
            return  # The peer closed cleanly: the stream is over.
        except InvalidPayloadError as exc:
            # The frame had a readable id, so the sender is told what was wrong; the generator
            # that raised is finished, and the next loop turn starts a fresh receive() on the
            # same open connection.
            await _answer_invalid(transport, voice, clock, exc)


async def _answer_invalid(
    transport: Transport, voice: HiveVoice, clock: Clock, error: InvalidPayloadError
) -> None:
    """Send the `control.error` that answers a frame whose payload failed validation."""
    if error.message_id is None or error.sender is None:
        return  # The frame cannot be addressed: nobody to tell.
    failed_kind = error.kind
    try:
        reply = wrap(
            ErrorMessage(
                code=error.code,
                message=str(error)[:MAX_ERROR_MESSAGE_CHARS],
                failed_kind=failed_kind,
                is_retryable=False,
            ),
            Hop(sender=voice.hive_id, recipient=error.sender, node_id=voice.hive_node_id),
            clock=clock,
            correlation_id=MessageId(error.message_id),
        )
    except ValidationError:
        return  # The sender or id the frame claimed is not even a valid address.
    try:
        # Network write: one small frame; a peer that cannot take it is gone, and the stream's
        # next read ends the connection.
        async with asyncio.timeout(ERROR_REPLY_TIMEOUT_S):
            await transport.send(reply)
    except (TimeoutError, TransportClosedError, ConnectionLostError):
        return
