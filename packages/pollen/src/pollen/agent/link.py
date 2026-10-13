"""Hold one connection to the Queen: dial with the pin, verify what arrives, carry what leaves.

The Pollen Packet (the thin gateway program on a device the Hive borrows) dials out to the Queen
(the Hive's central orchestrator) and never listens (gateway-contract section 5). A ``Link`` is
one such connection. It dials the persisted address once, over ``wss://`` with the certificate
pin from the invite, which replaces chain and name validation: a different certificate closes the
connection before any frame is sent. Every frame that arrives is verified against the trusted
nodes' keys by the codec, so the packet never reads an unverified one; the keys live in a
``LiveVerifier`` that the packet swaps when a QueenMoved relocation adds or drops a node. Frames
go out under a lock so the heartbeat, an exec's output and an outbox replay cannot interleave on
the wire, and a link satisfies waggle's ``Transport`` for exactly that replay.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built per connection attempt
    by pollen.agent.packet; used by pollen.agent.courier to send; calls into
    waggle.transport.websocket_client, waggle.codec and waggle.signing only.

Key invariants:
    - One dial attempt per ``connect``: backoff belongs to the packet's loop, not to the link.
    - Every send and the dial itself has a timeout from a named constant.
    - A frame that fails verification is never yielded: the transport closes the connection.
    - The verifier is replaced as a whole, never edited, so a frame is judged by one consistent
      set of keys.

See Also:
    - docs/waggle/gateway-contract.md sections 5 (the link) and 6 (signing and verifying).
    - pollen.agent.moved for the relocation notice that changes the trusted keys.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

from waggle.clock import Clock
from waggle.codec import Codec, Signer
from waggle.envelope import Envelope
from waggle.signing import Ed25519Verifier
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport

CONNECT_TIMEOUT_S = 15.0  # One dial: TCP, TLS and the upgrade (the transport bounds it at 10 s).
SEND_TIMEOUT_S = 30.0  # One frame leaving; a peer that stops reading is a lost link.
CLOSE_TIMEOUT_S = 12.0  # A close handshake (the transport bounds it at 10 s).

__all__ = ["CLOSE_TIMEOUT_S", "CONNECT_TIMEOUT_S", "SEND_TIMEOUT_S", "Link", "LiveVerifier"]


class LiveVerifier:
    """A verifier whose key set can be replaced while connections are open.

    Satisfies waggle.codec's ``Verifier`` structurally. Owns mutable state (codingrules 8.5,
    documented): ``_current`` is an immutable ``Ed25519Verifier`` that ``replace`` swaps whole.
    """

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        """Trust exactly the given nodes.

        Args:
            keys: ``node_id`` to raw 32-byte public key.
        """
        self._current = Ed25519Verifier(keys)

    def replace(self, keys: Mapping[str, bytes]) -> None:
        """Trust exactly ``keys`` from now on (a QueenMoved changed the set).

        Args:
            keys: ``node_id`` to raw 32-byte public key.
        """
        self._current = Ed25519Verifier(keys)

    def verify(self, node_id: str, canonical: bytes, signature: str) -> None:
        """Check a frame's signature against the current keys.

        Args:
            node_id: The envelope's node.
            canonical: The frame's canonical bytes.
            signature: The envelope's signature.

        Raises:
            SignatureError: The node is unknown or the signature does not verify.
        """
        self._current.verify(node_id, canonical, signature)


class Link:
    """One connection to the Queen.

    Satisfies waggle's ``Transport`` (it has ``is_connected``, ``connect``, ``send``, ``receive``
    and ``close``), which is what lets ``replay_outbox`` drain the outbox through it.
    """

    def __init__(
        self, uri: str, pin: str | None, signer: Signer, verifier: LiveVerifier, clock: Clock
    ) -> None:
        """Prepare a connection; nothing is dialled until ``connect``.

        Args:
            uri: The Queen's address: ``wss://`` anywhere, ``ws://`` on loopback only.
            pin: The certificate's SHA-256 from the invite; None only for ``ws://``.
            signer: Signs every frame this link sends.
            verifier: Requires and checks a signature on every frame it receives.
            clock: The injected clock the dial's transport uses.

        Raises:
            ValueError: The address breaks the endpoint rule, or the pin does not fit it.
        """
        self.uri = uri
        self._clock = clock
        self._opened_at: float | None = None  # Monotonic reading when the dial succeeded.
        codec = Codec(signer=signer, verifier=verifier)
        options = DialOptions(max_attempts=1, tls_pin_sha256=pin)
        self._transport = WebSocketClientTransport(uri, codec, clock, options=options)
        self._send_lock = asyncio.Lock()  # One frame at a time on the wire.

    @property
    def is_connected(self) -> bool:
        """Whether the connection is open and nobody has closed it."""
        return self._transport.is_connected

    @property
    def open_for_s(self) -> float:
        """Seconds since the dial succeeded while the connection stays open; 0.0 otherwise.

        The WebSocket keepalive fails a link whose Queen stops answering pings, so a link that
        has stayed open for a full ping interval has had a pong: this is how the dead-man
        switch counts a quiet but healthy Queen as heard from (gateway-contract section 13).
        """
        if self._opened_at is None or not self.is_connected:
            return 0.0
        return max(0.0, self._clock.monotonic() - self._opened_at)

    async def connect(self) -> None:
        """Dial once.

        Raises:
            ConnectFailedError: The Queen could not be reached.
            TlsPinMismatchError: The certificate presented is not the pinned one; no frame was
                sent.
            TimeoutError: The dial took longer than CONNECT_TIMEOUT_S.
        """
        async with asyncio.timeout(CONNECT_TIMEOUT_S):  # Network: TCP, TLS and the upgrade.
            await self._transport.connect()
        self._opened_at = self._clock.monotonic()

    async def send(self, envelope: Envelope) -> None:
        """Sign and send one frame.

        Args:
            envelope: A consistent envelope.

        Raises:
            FrameTooLargeError: The frame would exceed the wire limit; nothing was sent.
            TransportError: The link is closed or lost.
            TimeoutError: The peer did not take the frame within SEND_TIMEOUT_S.
        """
        async with self._send_lock, asyncio.timeout(SEND_TIMEOUT_S):
            await self._transport.send(envelope)

    def receive(self) -> AsyncIterator[Envelope]:
        """Yield each verified envelope the Queen sends until the connection ends.

        Returns:
            The envelopes in arrival order. No timeout of its own: the WebSocket keepalive fails
            a silent link and ends this with an error, and a quiet Queen is not a fault.
        """
        return self._transport.receive()

    async def close(self) -> None:
        """Close the connection politely; a second call does nothing."""
        async with asyncio.timeout(CLOSE_TIMEOUT_S):  # The handshake; the transport drops TCP.
            await self._transport.close()
