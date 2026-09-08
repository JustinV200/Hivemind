"""Provide ContractLink: one link shape for the transport conformance suite, over both transports.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and a
Transport carries its frames (the bytes the codec makes of one Envelope, the outer wrapper every
message travels in) between two bees. The conformance suite of spec section 11 states the
transport contract once and runs it over every implementation; this module is what makes that
possible. A ``ContractLink`` is a connected client end and server end plus the three things a
contract test does to the link itself that no Transport method offers: deliver raw bytes to the
receiving end as a hostile peer would, take the link away, and bring it back so a reconnect and
an outbox replay can be tested. ``MemoryLink`` maps those onto ``MemoryTransport.pair`` and its
``inject_frame`` and ``drop`` hooks; ``WebSocketLink`` starts a real loopback ``WebSocketServer``,
dials it with a ``WebSocketClientTransport``, sends raw bytes through a bare ``websockets``
client, drops the link by closing the listener and restarts it on the same port. The suite loads
this module by file path (the test tree has no packages), so it imports nothing from a sibling.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by tests/contracts/test_transport_contract.py
    only; calls into waggle.transport.memory, waggle.transport.websocket_client,
    waggle.transport.websocket_server and the websockets library.

Key invariants:
    - Both links satisfy the same ContractLink protocol, so a contract test never branches on
      the kind it is running over.
    - Every await on the network is bounded by WAIT_S, and a dial gives up after DIAL_TIMEOUT_S
      of real time, so a broken listener fails a test in about a second rather than hanging.
    - aclose() leaves no listener, connection or raw client open, whatever the test did first.

See Also:
    - waggle.transport.base for the contract the suite checks.
    - tests/transport/test_memory.py and test_websocket.py for each implementation's own tests.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from websockets.asyncio.client import ClientConnection, connect

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.transport.base import Transport
from waggle.transport.memory import MemoryTransport
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_client import WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

KINDS = ("memory", "websocket")  # Every Transport that ships; the suite runs once per entry.
WAIT_S = 5.0  # Bounds every await on the network; loopback answers in milliseconds.
# Real seconds one dial may take. Loopback connects in milliseconds; the small bound keeps a
# failed dial short on Windows, where a refused connection takes about two seconds otherwise.
DIAL_TIMEOUT_S = 1.0

__all__ = [
    "DIAL_TIMEOUT_S",
    "KINDS",
    "WAIT_S",
    "ContractLink",
    "MemoryLink",
    "WebSocketLink",
    "open_link",
]


class ContractLink(Protocol):
    """A connected client end and server end, plus what a contract test does to the link itself.

    The three hooks are what no Transport method offers: deliver raw bytes as a hostile peer
    would, take the link away, and bring it back. Each implementation maps them onto its own
    transport's affordances so a test never branches on the kind.
    """

    @property
    def client(self) -> Transport:
        """The end a remote bee holds: it sends, and replays its outbox after a reconnect.

        Returns:
            The current client-side transport; a new one after ``restart`` on the memory link.
        """
        ...

    @property
    def server(self) -> Transport:
        """The end the Hive Stand holds for this client.

        Returns:
            The current server-side transport; a new one after ``restart``.
        """
        ...

    async def inject_malformed(self, frame: bytes) -> Transport:
        """Deliver raw bytes to a server-side end as a hostile peer would.

        Args:
            frame: The bytes the receiving end's codec will see next, exactly as given.

        Returns:
            The server-side transport whose next ``receive`` decodes ``frame``.
        """
        ...

    async def drop_link(self) -> None:
        """Take the link away under both ends, with no clean close from the client.

        Returns:
            None, once the client's next send is bound to fail.
        """
        ...

    async def restart(self) -> None:
        """Bring the link back and reconnect, so ``client`` and ``server`` carry frames again.

        Returns:
            None, once both ends are connected.
        """
        ...

    async def aclose(self) -> None:
        """Release everything the link holds; safe after a drop and safe to call twice.

        Returns:
            None.
        """
        ...


class MemoryLink:
    """The in-process pair as a ContractLink: its own test hooks are the drop and the injection."""

    def __init__(self, client_codec: Codec, server_codec: Codec) -> None:
        """Build a connected pair on the two codecs.

        Args:
            client_codec: The client end's codec; signs its sends and verifies its receives.
            server_codec: The server end's codec.
        """
        # Kept for restart, which is a fresh pair: a dropped pair cannot be revived.
        self._client_codec = client_codec
        self._server_codec = server_codec
        self._client, self._server = MemoryTransport.pair(client_codec, server_codec)

    @property
    def client(self) -> Transport:
        """The client end of the current pair.

        Returns:
            The end that sends what ``server`` receives.
        """
        return self._client

    @property
    def server(self) -> Transport:
        """The server end of the current pair.

        Returns:
            The end that receives what ``client`` sends.
        """
        return self._server

    async def inject_malformed(self, frame: bytes) -> Transport:
        """Put ``frame`` on the server end's inbox as if the client had sent it.

        Args:
            frame: Raw bytes; the server end's codec decodes them on its next receive.

        Returns:
            The server end.
        """
        self._server.inject_frame(frame)
        return self._server

    async def drop_link(self) -> None:
        """Simulate link loss: drop() on one end drops the pair.

        Returns:
            None.
        """
        self._client.drop()

    async def restart(self) -> None:
        """Replace the dropped pair with a fresh one on the same codecs.

        Returns:
            None; ``client`` and ``server`` now name the new ends.
        """
        self._client, self._server = MemoryTransport.pair(self._client_codec, self._server_codec)

    async def aclose(self) -> None:
        """Close both ends; each close is idempotent and harmless after a drop.

        Returns:
            None.
        """
        await self._client.close()
        await self._server.close()


class WebSocketLink:
    """A loopback listener and a dialled client as a ContractLink; build one with ``open``."""

    def __init__(
        self,
        server_codec: Codec,
        listener: WebSocketServer,
        client: WebSocketClientTransport,
        server: WebSocketTransport,
    ) -> None:
        """Wrap an already started listener, dialled client and accepted server transport.

        Args:
            server_codec: What a restarted listener is built with.
            listener: The started listener the client dialled.
            client: The connected client transport.
            server: The transport the listener accepted for ``client``.
        """
        self._server_codec = server_codec
        self._listener = listener
        # Read now: a closed listener has no socket to read the port from, and restart needs it.
        self._port = listener.port
        self._client = client
        self._server = server
        self._raw_clients: list[ClientConnection] = []  # Hostile peers, closed by aclose.

    @classmethod
    async def open(cls, client_codec: Codec, server_codec: Codec) -> WebSocketLink:
        """Start a listener on an OS-assigned loopback port, dial it, and accept the connection.

        Args:
            client_codec: The dialling transport's codec.
            server_codec: The listener's codec, given to every accepted transport.

        Returns:
            A connected link.
        """
        listener = WebSocketServer(server_codec)
        await listener.start()
        # One attempt: the listener is up, so a failed dial is a bug, and a FakeClock means no
        # backoff sleep could ever hang the suite.
        client = WebSocketClientTransport(
            listener.uri,
            client_codec,
            FakeClock(),
            max_attempts=1,
            open_timeout_s=DIAL_TIMEOUT_S,
        )
        async with asyncio.timeout(WAIT_S):
            await client.connect()
        return cls(server_codec, listener, client, await _accept(listener))

    @property
    def client(self) -> Transport:
        """The dialled client transport; the same object across a restart.

        Returns:
            The client-side transport.
        """
        return self._client

    @property
    def server(self) -> Transport:
        """The transport the current listener accepted for the client.

        Returns:
            The server-side transport.
        """
        return self._server

    async def inject_malformed(self, frame: bytes) -> Transport:
        """Dial the listener with a bare websockets client and send ``frame`` on it, raw.

        Args:
            frame: Raw bytes, sent as one binary WebSocket frame.

        Returns:
            The transport the listener accepted for that client; its next receive sees the
            frame. The raw client stays open until ``aclose`` so the refusal is observable.
        """
        async with asyncio.timeout(WAIT_S):
            # proxy=None as the real dial passes: a proxy variable must not reroute loopback.
            raw = await connect(self._listener.uri, proxy=None)
        self._raw_clients.append(raw)
        accepted = await _accept(self._listener)
        async with asyncio.timeout(WAIT_S):
            await raw.send(frame)
        return accepted

    async def drop_link(self) -> None:
        """Close the listener: every connection ends and the port is free for ``restart``.

        Returns:
            None, once every handler has finished.
        """
        async with asyncio.timeout(WAIT_S):
            await self._listener.close()

    async def restart(self) -> None:
        """Start a new listener on the same port and have the same client dial it afresh.

        Returns:
            None; ``server`` now names the newly accepted transport.
        """
        listener = WebSocketServer(self._server_codec, port=self._port)
        await listener.start()
        self._listener = listener
        async with asyncio.timeout(WAIT_S):
            await self._client.connect()
        self._server = await _accept(listener)

    async def aclose(self) -> None:
        """Close the raw clients, the client and the listener, each of which is idempotent.

        Returns:
            None.
        """
        async with asyncio.timeout(WAIT_S):
            for raw in self._raw_clients:
                await raw.close()
            self._raw_clients.clear()
            await self._client.close()
            await self._listener.close()


async def open_link(kind: str, client_codec: Codec, server_codec: Codec) -> ContractLink:
    """Build a connected link of ``kind`` on the two codecs.

    Args:
        kind: One of KINDS.
        client_codec: The client end's codec.
        server_codec: The server end's codec.

    Returns:
        A ContractLink ready to carry frames.

    Raises:
        ValueError: ``kind`` is not in KINDS.
    """
    if kind == "memory":
        return MemoryLink(client_codec, server_codec)
    if kind == "websocket":
        return await WebSocketLink.open(client_codec, server_codec)
    raise ValueError(f"Unknown transport kind {kind!r}; expected one of {KINDS}.")


async def _accept(listener: WebSocketServer) -> WebSocketTransport:
    """The next connection ``listener`` accepted."""
    async with asyncio.timeout(WAIT_S):
        listing = listener.connections()
        try:
            return await anext(listing)
        finally:
            await listing.aclose()
