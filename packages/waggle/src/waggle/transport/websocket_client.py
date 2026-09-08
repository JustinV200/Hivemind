"""Provide WebSocketClientTransport: dial the Hive Stand with capped backoff, then carry frames.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Every
remote bee dials OUT to the Hive Stand (the machine the Queen, the central orchestrator, runs
on): a Warden (the always-on supervisor of one Cell, a unit of compute) from a Virtual Cell
that exposes no inbound port, and a Pollen Packet (the thin gateway on an enrolled device) from
a machine that opens nothing. This transport is that dial. ``connect`` tries the URI up to
``max_attempts`` times with capped exponential backoff, every wait routed through the injected
Clock so a test drives it with a FakeClock and no real timer, and every attempt bounded in real
time by websockets' ``open_timeout``; it raises ``ConnectFailedError`` when the attempts run
out. Once connected, ``send``, ``receive`` and ``close`` delegate to a ``WebSocketTransport``
around the one connection. There is no automatic reconnect inside ``receive``: when the link
drops the caller's loop calls ``connect`` again, which dials afresh, and then replays its outbox
(spec section 10), so what was lost and what is done about it stays visible in the caller's code.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Satisfies waggle.transport.base.Transport; used by
    every Warden and Pollen loop that reaches the Hive Stand; calls into waggle.uris,
    waggle.clock, waggle.transport.websocket and the websockets library.

Key invariants:
    - A ws:// URI is accepted only on a loopback host (waggle.uris): confidentiality is the
      link's, and a plaintext link exists only inside one machine (spec section 6).
    - Every backoff sleep goes through the injected Clock; the only real-time wait is the
      per-attempt open_timeout websockets enforces on the handshake.
    - The dial passes the codec's max_frame_bytes as websockets' max_size, so an oversized
      incoming frame is refused with 1009 before it reaches the codec.

See Also:
    - waggle.transport.websocket for the transport this one wraps once connected.
    - waggle.transport.websocket_server for the one listener every client dials.
    - waggle.loop for the same backoff sequence applied to a bee's whole tick loop.
    - docs/adr/0004-waggle-transport-websocket-json.md for why reconnect stays in the caller.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidHandshake

from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import ConnectFailedError, ConnectionLostError, TransportClosedError
from waggle.transport.websocket import (
    CLOSE_TIMEOUT_S,
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    WebSocketTransport,
)
from waggle.uris import check_waggle_uri

RECONNECT_INITIAL_S = 0.5  # First wait after a failed dial; the sequence ADR-0004 documents.
RECONNECT_FACTOR = 2.0  # Each failed dial doubles the wait: 0.5, 1, 2, 4, 8, 16, 30, 30, ...
RECONNECT_MAX_S = 30.0  # The cap on the wait: a returning Hive Stand is found within 30 s.
OPEN_TIMEOUT_S = 10.0  # Real time per attempt: TCP connect plus handshake (websockets' default).
DEFAULT_MAX_ATTEMPTS = 5  # Four waits (7.5 s in all) before giving up; the caller's loop redials.

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "OPEN_TIMEOUT_S",
    "RECONNECT_FACTOR",
    "RECONNECT_INITIAL_S",
    "RECONNECT_MAX_S",
    "WebSocketClientTransport",
]


class WebSocketClientTransport:
    """Dial one URI with capped backoff and carry frames over the connection that results.

    Concurrency model: one task calls ``connect``; afterwards one task sends and one receives,
    as the inner WebSocketTransport requires.
    """

    def __init__(
        self,
        uri: str,
        codec: Codec,
        clock: Clock,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        open_timeout_s: float = OPEN_TIMEOUT_S,
    ) -> None:
        """Remember where to dial and how; nothing is dialled until ``connect``.

        Args:
            uri: The Hive Stand's listener, ``wss://host:port`` anywhere or ``ws://`` on a
                loopback host only.
            codec: Encodes every send and decodes every receive; its limit is the dial's
                max_size.
            clock: The injected Clock every backoff wait goes through.
            max_attempts: Dials per ``connect`` before ConnectFailedError; at least 1.
            open_timeout_s: Real seconds one attempt may spend on TCP connect plus the
                handshake before it counts as failed.

        Raises:
            ValueError: ``uri`` is not a WebSocket URI, uses ws:// off loopback, or
                ``max_attempts`` is below 1.
        """
        # Checked once here, not per dial, so a misconfigured address fails at construction in
        # the composition root rather than inside a reconnect loop (spec section 6).
        self._uri = check_waggle_uri(uri)
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}.")
        self._codec = codec
        self._clock = clock
        self._max_attempts = max_attempts
        self._open_timeout_s = open_timeout_s
        self._inner: WebSocketTransport | None = None  # The current connection, once dialled.
        self._closed = False  # close() was called since the last connect().

    @property
    def uri(self) -> str:
        """The URI this transport dials.

        Returns:
            The URI given at construction, unchanged.
        """
        return self._uri

    @property
    def is_connected(self) -> bool:
        """Whether a dialled connection is open and unclosed.

        Returns:
            True between a successful ``connect`` and the first close or drop.
        """
        return self._inner is not None and self._inner.is_connected

    async def connect(self) -> None:
        """Dial until a connection opens or ``max_attempts`` are spent, backing off between.

        Returns at once when already connected. After a drop or a ``close``, dials afresh: the
        caller then replays its outbox on the new connection.

        Returns:
            None, once a connection is open.

        Raises:
            ConnectFailedError: Every attempt failed (refused, unreachable, handshake rejected
                or timed out); the message carries the URI and the attempt count.
            InvalidURI: The URI passed the loopback rule but websockets cannot parse it; a
                configuration error, never retried.
        """
        if self.is_connected:
            return
        self._closed = False
        delay = RECONNECT_INITIAL_S
        attempt = 0
        while True:
            attempt += 1
            # OSError covers a refused or unreachable host and the TimeoutError websockets
            # raises when open_timeout expires; InvalidHandshake is a peer that is not a Waggle
            # listener (yet). Any of them may clear when the Hive Stand comes back.
            try:
                connection = await self._dial()
            except (OSError, InvalidHandshake) as exc:
                if attempt >= self._max_attempts:
                    raise ConnectFailedError(
                        f"Could not connect to {self._uri} after {attempt} attempts; the last "
                        f"failure was {type(exc).__name__}: {exc}."
                    ) from exc
                # Through the injected Clock, never asyncio.sleep, so a FakeClock test drives
                # the whole sequence without a real timer (codingrules 14.5).
                await self._clock.sleep(delay)
                delay = min(delay * RECONNECT_FACTOR, RECONNECT_MAX_S)
                continue
            self._inner = WebSocketTransport(connection, self._codec)
            return

    async def send(self, envelope: Envelope) -> None:
        """Send ``envelope`` on the current connection.

        Args:
            envelope: A consistent Envelope; the codec signs it when configured to.

        Raises:
            FrameTooLargeError: The frame would exceed the codec's limit; nothing was sent.
            TransportClosedError: ``close`` was called, or the peer closed cleanly.
            ConnectionLostError: Not connected (``connect`` not yet called, or the link
                dropped).
        """
        await self._require_connection().send(envelope)

    async def receive(self) -> AsyncGenerator[Envelope, None]:
        """Yield each envelope the Hive Stand sends on the current connection.

        Returns:
            The envelopes in the order the peer sent them; ends normally on a clean close.

        Raises:
            TransportClosedError: ``close`` was called since the last ``connect``.
            ConnectionLostError: Not connected, or the link dropped.
            CodecError: A frame failed to decode (see WebSocketTransport.receive).
            SignatureError: A frame failed the codec's signature policy.
        """
        async for envelope in self._require_connection().receive():
            yield envelope

    async def close(self) -> None:
        """Close the current connection with code 1000; a second call does nothing.

        Returns:
            None. ``connect`` may be called again afterwards to dial afresh.
        """
        if self._closed:
            return
        self._closed = True
        # Never connected: nothing to close, but the flag above still makes send raise closed.
        if self._inner is not None:
            await self._inner.close()

    async def _dial(self) -> ClientConnection:
        """Make one dial attempt: TCP connect plus the WebSocket handshake."""
        # Network: milliseconds on loopback, seconds over a VPN or Tor route; open_timeout is
        # the real-time bound and raises TimeoutError (an OSError) when it expires. max_size
        # is the codec's limit so websockets refuses an oversized frame (1009) before the codec
        # sees it. proxy=None because a Hive link is direct or over the tier's own VPN or Tor
        # path (spec section 9): an HTTP(S)_PROXY variable must never reroute it silently.
        return await connect(
            self._uri,
            open_timeout=self._open_timeout_s,
            max_size=self._codec.max_frame_bytes,
            ping_interval=PING_INTERVAL_S,
            ping_timeout=PING_TIMEOUT_S,
            close_timeout=CLOSE_TIMEOUT_S,
            proxy=None,
        )

    def _require_connection(self) -> WebSocketTransport:
        """Return the current connection, or raise the error the contract gives for none."""
        if self._closed:
            raise TransportClosedError(
                f"The client transport to {self._uri} was closed; call connect() to dial again."
            )
        if self._inner is None:
            raise ConnectionLostError(
                f"The client transport to {self._uri} is not connected; call connect() first."
            )
        return self._inner
