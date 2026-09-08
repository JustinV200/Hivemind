"""Provide WebSocketTransport: one open websockets connection, either side, carrying frames.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and a
Transport carries its frames (the bytes the codec makes of one Envelope, the outer wrapper every
message travels in) between two bees. This is the transport for two bees in different processes
or on different machines: a Warden (the always-on supervisor of one Cell, a unit of compute) or
a Pollen Packet (the thin gateway on an enrolled device) dialling the Hive Stand (the machine
the Queen, the central orchestrator, runs on). It wraps exactly one connection the ``websockets``
library already opened, whichever side opened it (``waggle.transport.websocket_client`` dials,
``waggle.transport.websocket_server`` accepts; both hand their connection here), and is
duck-typed on the ``send``, ``recv`` and ``close`` API the two sides share. Frames travel as
WebSocket binary frames only, one envelope per frame; a text frame is a malformed frame. A frame
the codec refuses closes the connection with the code ``waggle.transport.base`` maps it to, and
a frame ``websockets`` itself refused for size (its ``max_size`` is the codec's limit) is
reported as the codec would have: ``FrameTooLargeError`` after a 1009 close.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Satisfies waggle.transport.base.Transport; built by
    waggle.transport.websocket_client and waggle.transport.websocket_server; calls into
    waggle.codec, waggle.transport.base and the websockets library. Latency class: a loopback
    round trip is under a millisecond; a VPN or Tor route is tens to hundreds of milliseconds.

Key invariants:
    - Exactly one connection per instance, never reopened: after a close or a drop the caller
      dials or accepts a new one and builds a new transport around it.
    - Every frame sent is binary and passed through the codec first; every frame received
      passes through the codec before it is yielded.
    - close() sends close code 1000 at most once; a decode failure sends its mapped code at most
      once; after either, send raises TransportClosedError.

See Also:
    - waggle.transport.base for the delivery contract and the close-code table.
    - waggle.transport.websocket_client and waggle.transport.websocket_server for the two
      ways a connection comes to exist.
    - docs/adr/0004-waggle-transport-websocket-json.md for the transport decision.
    - docs/waggle/spec.md sections 5 (binary frames, the size limit) and 9 (close codes).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import NoReturn

from websockets.asyncio.connection import Connection
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.protocol import State

from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import (
    CodecError,
    ConnectionLostError,
    FrameTooLargeError,
    MalformedFrameError,
    SignatureError,
    TransportClosedError,
)
from waggle.transport.base import CLOSE_MESSAGE_TOO_BIG, CLOSE_NORMAL, close_code_for

PING_INTERVAL_S = 20.0  # A link silent this long gets a WebSocket ping (websockets' default).
PING_TIMEOUT_S = 20.0  # No pong within this fails the link: 40 s at most from the last traffic.
CLOSE_TIMEOUT_S = 10.0  # The most a close waits for the peer's close frame before dropping TCP.

__all__ = ["CLOSE_TIMEOUT_S", "PING_INTERVAL_S", "PING_TIMEOUT_S", "WebSocketTransport"]


class WebSocketTransport:
    """Carry frames over one open websockets connection, client or server side.

    Concurrency model: one task sends and one task receives at a time, as websockets itself
    requires (a second concurrent ``recv`` raises inside the library); the keepalive ping runs
    in websockets' own task with the intervals the dial or the listener gave it.
    """

    def __init__(self, connection: Connection, codec: Codec) -> None:
        """Wrap an already open connection with the codec that reads and writes its frames.

        Args:
            connection: A websockets ClientConnection or ServerConnection in the OPEN state.
            codec: Encodes every send and decodes every receive; its policy decides signatures.
        """
        self._connection = connection
        self._codec = codec
        self._closed = False  # This end sent a close frame (1000 or a mapped code).
        # Captured now because websockets forgets the peer's address once the socket is gone,
        # and the address is most wanted in the log line written after a failure.
        self._remote_address = _format_address(connection.remote_address)

    @property
    def is_connected(self) -> bool:
        """Whether frames can still flow: neither side has started closing.

        Returns:
            True while the connection is OPEN and this end has not closed it.
        """
        return not self._closed and self._connection.state is State.OPEN

    @property
    def remote_address(self) -> str:
        """The peer's address as ``host:port``, for log lines; never a secret.

        Returns:
            The address websockets reported when the connection was wrapped.
        """
        return self._remote_address

    async def connect(self) -> None:
        """Do nothing: the connection was opened by the dial or the accept that made this.

        Returns:
            None. A closed connection is never reopened here; dial or accept a new one.
        """
        return None

    async def send(self, envelope: Envelope) -> None:
        """Encode ``envelope`` and send it as one binary frame.

        Args:
            envelope: A consistent Envelope; the codec signs it when configured to.

        Raises:
            FrameTooLargeError: The frame would exceed the codec's limit; nothing was sent.
            TransportClosedError: This end closed, or the peer closed cleanly.
            ConnectionLostError: The link dropped or the peer closed with a protocol code.
        """
        if self._closed:
            raise TransportClosedError(
                f"The WebSocket transport to {self._remote_address} is closed; nothing can be sent."
            )
        frame = self._codec.encode(envelope)
        # Network write: microseconds to buffer, milliseconds on loopback to drain; websockets'
        # write limit applies backpressure and its keepalive fails a dead peer within
        # PING_INTERVAL_S + PING_TIMEOUT_S, after which this raises ConnectionClosed. bytes
        # always go as a binary frame, which is the only frame kind Waggle allows.
        try:
            await self._connection.send(frame)
        except ConnectionClosedOK as exc:
            raise TransportClosedError(
                f"The peer at {self._remote_address} closed the connection cleanly before the "
                f"frame for envelope {envelope.id} was sent."
            ) from exc
        except ConnectionClosedError as exc:
            raise ConnectionLostError(
                f"The connection to {self._remote_address} was lost before the frame for "
                f"envelope {envelope.id} was sent: {exc}."
            ) from exc

    async def receive(self) -> AsyncGenerator[Envelope, None]:
        """Yield each frame the peer sends, decoded, until the connection ends.

        Returns:
            The envelopes in the order the peer sent them.

        Raises:
            ConnectionLostError: The link dropped, or ended by a protocol close.
            CodecError: A frame failed to decode; the connection is closed with the mapped
                code, except for InvalidPayloadError, after which it stays open.
            SignatureError: A frame failed the codec's signature policy; closed with 1008.
        """
        while True:
            # Network read: resolves when a frame arrives or the connection ends; no timeout
            # of its own because a silent link is the keepalive's to detect (it fails the
            # connection, and this raises), and a quiet peer is not an error.
            try:
                message = await self._connection.recv()
            except ConnectionClosedOK:
                # Either side closed with 1000 or 1001: the stream ended as the contract says.
                return
            except ConnectionClosedError as exc:
                raise self._ended_abnormally(exc) from exc
            # A text frame is the one framing mistake a peer can make (spec section 5).
            if isinstance(message, str):
                await self._refuse(
                    MalformedFrameError(
                        f"The peer at {self._remote_address} sent a text frame; Waggle frames "
                        "are binary."
                    )
                )
            yield await self._decode(message)

    async def close(self) -> None:
        """Send close code 1000 and wait for the handshake; a second call does nothing.

        Returns:
            None, once the close has completed or CLOSE_TIMEOUT_S passed without the peer's
            close frame (websockets then drops the TCP stream; nothing is raised).
        """
        if self._closed:
            return
        await self._close_with(CLOSE_NORMAL, "")

    async def _decode(self, frame: bytes) -> Envelope:
        """Decode one frame, closing with the mapped code when the codec refuses it."""
        try:
            return self._codec.decode(frame)
        except (CodecError, SignatureError) as exc:
            await self._refuse(exc)

    async def _refuse(self, error: CodecError | SignatureError) -> NoReturn:
        """Close with the code ``error`` maps to (if any) and raise it."""
        # None means the frame had a readable id: the caller answers with a control.error and
        # the connection stays open (spec section 7); anything else ends the connection.
        code = close_code_for(error)
        if code is not None:
            # The stable error code travels as the close reason so the peer's log names the
            # fault without content: a reason is limited to 123 bytes and never carries a frame.
            await self._close_with(code, error.code)
        raise error

    async def _close_with(self, code: int, reason: str) -> None:
        """Mark this end closed and run the close handshake with ``code``."""
        self._closed = True
        # Network round trip: the close frame and the peer's echo; bounded by CLOSE_TIMEOUT_S,
        # given to websockets by the dial or the listener, after which the TCP stream is
        # dropped. Never raises: websockets treats closing a closed connection as a no-op.
        await self._connection.close(code, reason)

    def _ended_abnormally(
        self, exc: ConnectionClosedError
    ) -> ConnectionLostError | FrameTooLargeError:
        """Map a non-clean close to the error ``receive`` raises for it."""
        # websockets refused an incoming frame over max_size (the codec's limit) and closed
        # with 1009 on this end's behalf before the bytes reached the codec: report it as the
        # codec would, so both transports raise the same error for the same fault. A 1009 that
        # arrived first (rcvd_then_sent) is the peer refusing one of ours, a lost link here.
        if (
            exc.sent is not None
            and exc.sent.code == CLOSE_MESSAGE_TOO_BIG
            and not exc.rcvd_then_sent
        ):
            self._closed = True
            return FrameTooLargeError(
                f"The peer at {self._remote_address} sent a frame over the limit of "
                f"{self._codec.max_frame_bytes} bytes; it was refused and the connection closed "
                f"with code {CLOSE_MESSAGE_TOO_BIG}."
            )
        return ConnectionLostError(f"The connection to {self._remote_address} was lost: {exc}.")


def _format_address(address: object) -> str:
    """Render websockets' remote_address (a socket tuple, or None once gone) as ``host:port``."""
    # A TCP peername is (host, port) for IPv4 and (host, port, flow, scope) for IPv6; only the
    # first two parts identify the peer in a log line.
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return str(address)
