"""Define the Transport protocol: the policy-free carrier of Waggle frames between two bees.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A frame
is the bytes ``waggle.codec.Codec`` produces from one Envelope (the outer wrapper every message
travels in) and turns back into one. A transport moves those bytes between two ends, whether the
Queen (the central orchestrator), a Warden (the always-on supervisor of one Cell, a unit of
compute), a Worker (a subagent a Warden spawns) or a Pollen Packet (the thin gateway on an
enrolled device), and does nothing else: every check (size, shape, version, kind, signature) is
the codec's, and every retry and every durable copy is the caller's, through the outbox (spec
section 10). This module holds the protocol every implementation satisfies, the delivery
contract they share, and the one table that maps a failed decode to the WebSocket close code a
transport sends, so the in-memory pair and the WebSocket transport end a bad connection
identically and the conformance suite can be parametrised over both.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Satisfied by waggle.transport.memory and
    waggle.transport.websocket (whose connections the client and server modules produce);
    depended on by every bee loop that sends or receives and by the outbox replay; calls into
    waggle.errors only.

Key invariants:
    - Delivery is at-most-once at this layer, with ordering preserved within one connection; a
      transport never retries, never buffers across connections and never persists anything.
    - A frame that fails to decode closes the connection with the code close_code_for maps it
      to, except InvalidPayloadError, which leaves the connection open (spec section 7).
    - close() is idempotent on every implementation.

See Also:
    - docs/waggle/spec.md section 9 (transports) and section 7 (which failures close).
    - docs/adr/0004-waggle-transport-websocket-json.md for the transport decision.
    - waggle.transport.memory and waggle.transport.websocket for the implementations.
    - waggle.codec for the checks a transport never repeats.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from types import MappingProxyType
from typing import Protocol

from waggle.envelope import Envelope
from waggle.errors import (
    CodecError,
    FrameTooLargeError,
    InvalidPayloadError,
    SignatureError,
    WaggleError,
)

CLOSE_NORMAL = 1000  # RFC 6455 7.4.1: a deliberate close(); the peer's receive ends normally.
CLOSE_PROTOCOL_ERROR = 1002  # A malformed frame, an unsupported major or an unknown kind.
CLOSE_POLICY_VIOLATION = 1008  # A missing, unknown or invalid signature: the codec's policy.
CLOSE_MESSAGE_TOO_BIG = 1009  # A frame over the codec's max_frame_bytes.

# Decode failure -> close code, matched by isinstance IN THIS ORDER: the specific class comes
# before its family root because FrameTooLargeError is itself a CodecError. InvalidPayloadError
# is deliberately absent: that frame had a readable id, the receiver answers it with a
# control.error and the connection stays open (spec section 7), so close_code_for returns None.
CLOSE_CODE_FOR: Mapping[type[WaggleError], int] = MappingProxyType(
    {
        FrameTooLargeError: CLOSE_MESSAGE_TOO_BIG,
        SignatureError: CLOSE_POLICY_VIOLATION,
        CodecError: CLOSE_PROTOCOL_ERROR,
    }
)

__all__ = [
    "CLOSE_CODE_FOR",
    "CLOSE_MESSAGE_TOO_BIG",
    "CLOSE_NORMAL",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_PROTOCOL_ERROR",
    "Transport",
    "close_code_for",
]


class Transport(Protocol):
    """One connection between two bees that carries Envelopes as frames, in order, at most once.

    The delivery contract every implementation keeps (spec section 9):

    - **At-most-once.** A ``send`` that returns has handed the frame to the link; a ``send``
      that raises has not, and nothing is retried here. Ordering holds within one connection:
      what one end sends in sequence the other receives in sequence. Retries and durability
      belong to the caller through the outbox, which replays after a reconnect.
    - **Closed versus lost.** ``send`` after ``close`` (or after the peer's clean close) raises
      ``TransportClosedError``; ``send`` or ``receive`` on a dropped link raises
      ``ConnectionLostError``. ``receive`` ends normally when either side closes cleanly.
    - **A bad frame ends the connection.** A frame that fails to decode makes ``receive`` raise
      the ``CodecError`` or ``SignatureError`` and the transport closes with the code
      ``close_code_for`` gives (1002 malformed, unsupported major or unknown kind; 1008 a
      signature policy failure; 1009 too large): a bad frame from a peer is a bug or an attack,
      never a flaky link, and reconnect plus outbox replay handles the aftermath. The one
      exception is ``InvalidPayloadError``: the frame had a readable id, ``receive`` raises it,
      the connection stays open and calling ``receive`` again continues on it, so the caller
      can answer with a ``control.error`` (spec section 7).
    - ``close`` is idempotent; ``connect`` on an already connected transport is a no-op.

    Concurrency model: one task sends and one task receives at a time; two tasks sending on
    one transport interleave frames unpredictably and are the caller's bug.
    """

    @property
    def is_connected(self) -> bool:
        """Whether frames can still flow: connected, not closed by either side, not dropped.

        Returns:
            True while ``send`` may succeed; False after ``close``, the peer's close, or a drop.
        """
        ...

    async def connect(self) -> None:
        """Establish the connection, or return at once when it is already established.

        Raises:
            ConnectFailedError: A dialling transport gave up after its attempts.
        """
        ...

    async def send(self, envelope: Envelope) -> None:
        """Encode ``envelope`` through the codec and hand the frame to the link.

        Args:
            envelope: A consistent Envelope; the codec signs it when configured to.

        Raises:
            FrameTooLargeError: The frame would exceed the codec's limit; nothing was sent.
            TransportClosedError: This end, or the peer, closed cleanly before the send.
            ConnectionLostError: The link dropped; the caller reconnects and replays.
        """
        ...

    def receive(self) -> AsyncIterator[Envelope]:
        """Yield each decoded Envelope in arrival order until the connection ends.

        An async generator method: ``async for envelope in transport.receive()``. It ends
        normally on a clean close from either side.

        Returns:
            The envelopes the peer sent, in order.

        Raises:
            ConnectionLostError: The link dropped, or ended by a protocol close.
            CodecError: A frame failed to decode; the connection is closed with the mapped
                code, except for InvalidPayloadError, after which it stays open.
            SignatureError: A frame failed the codec's signature policy; closed with 1008.
        """
        ...

    async def close(self) -> None:
        """End the connection cleanly (close code 1000); a second call does nothing.

        Returns:
            None, once the close has been handed to the link.
        """
        ...


def close_code_for(error: CodecError | SignatureError) -> int | None:
    """Return the close code a transport sends after ``error``, or None to stay connected.

    Args:
        error: The failure ``Codec.decode`` raised for the frame just received.

    Returns:
        The RFC 6455 close code from CLOSE_CODE_FOR, or None for InvalidPayloadError, the one
        decode failure that leaves the connection open (spec section 7).
    """
    # The readable-id case first: it is a CodecError, and the table would otherwise map it to a
    # protocol close that the spec says must not happen.
    if isinstance(error, InvalidPayloadError):
        return None
    # First match in table order; a decode failure the table does not name is still a protocol
    # error, the code the spec gives every "cannot read this frame" case.
    return next(
        (code for error_class, code in CLOSE_CODE_FOR.items() if isinstance(error, error_class)),
        CLOSE_PROTOCOL_ERROR,
    )
