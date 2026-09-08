"""Provide WebSocketServer: the Hive Stand's one Waggle listener, handing out transports.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The Hive
has exactly one listener for it, on the Hive Stand (the machine the Queen, the central
orchestrator, runs on): every Warden (the always-on supervisor of one Cell, a unit of compute)
and every Pollen Packet (the thin gateway on an enrolled device) dials OUT to it, so a Virtual
Cell keeps its no-inbound-ports default and a Real Cell (a borrowed device) opens nothing
(codingrules section 15). This module is that listener. ``start`` binds and serves; each
accepted connection is wrapped in a ``WebSocketTransport``, queued for ``connections`` to yield,
and kept alive by its handler until the consumer closes the transport or the peer disconnects;
``close`` ends every open connection with 1000, stops the listener and waits for every handler
to finish. The server never creates a task of its own: websockets owns the handler tasks and
``wait_closed`` awaits them, so nothing is ever dropped (codingrules section 11). It binds
loopback by default and speaks plaintext ws://; a listener reachable off the machine is fronted
by the tier's VPN or TLS (spec section 6), which a later phase wires through the manifest.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Started by the Hive Stand's composition root; yields
    waggle.transport.websocket.WebSocketTransport to the Queen's accept loop; calls into
    waggle.codec, waggle.transport.websocket and the websockets library.

Key invariants:
    - Every connection accepted is either yielded by connections() or closed by close(); none
      is leaked, and every handler task ends before close() returns.
    - The listener passes the codec's max_frame_bytes as websockets' max_size, so an oversized
      frame is refused with 1009 before it reaches the codec.
    - start() and close() are idempotent; a closed server is not restarted (make a new one).

See Also:
    - waggle.transport.websocket for the transport each accepted connection becomes.
    - waggle.transport.websocket_client for the dial every remote bee makes to this listener.
    - docs/adr/0004-waggle-transport-websocket-json.md for the one-listener decision.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from websockets.asyncio.server import Server, ServerConnection, serve

from waggle.codec import Codec
from waggle.transport.websocket import (
    CLOSE_TIMEOUT_S,
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    WebSocketTransport,
)

DEFAULT_HOST = "127.0.0.1"  # Loopback: nothing listens off the machine until a tier fronts it.
OS_ASSIGNED_PORT = 0  # Let the OS pick a free port; read it back from the port property.
OPEN_TIMEOUT_S = 10.0  # Real time a peer has to finish the handshake (websockets' default).

__all__ = ["DEFAULT_HOST", "OPEN_TIMEOUT_S", "OS_ASSIGNED_PORT", "WebSocketServer"]


class WebSocketServer:
    """Listen for Waggle connections and hand each out as a WebSocketTransport.

    Concurrency model: one task consumes ``connections``; each accepted connection then belongs
    to whatever task the consumer hands it to, and ``close`` may be called from any task.
    """

    def __init__(
        self,
        codec: Codec,
        *,
        host: str = DEFAULT_HOST,
        port: int = OS_ASSIGNED_PORT,
        open_timeout_s: float = OPEN_TIMEOUT_S,
    ) -> None:
        """Remember where to listen and how; nothing is bound until ``start``.

        Args:
            codec: Given to every accepted transport; its limit is the listener's max_size.
            host: The interface to bind; loopback by default.
            port: The port to bind; 0 lets the OS choose one.
            open_timeout_s: Real seconds a peer has to complete the handshake.
        """
        self._codec = codec
        self._host = host
        self._port = port
        self._open_timeout_s = open_timeout_s
        self._server: Server | None = None  # The websockets listener, once started.
        self._closed = False
        # Accepted transports wait here for connections(); None ends that iteration. Unbounded,
        # so a handler's put never blocks and a burst of dials is never refused at this layer.
        self._accepted: asyncio.Queue[WebSocketTransport | None] = asyncio.Queue()
        self._open: set[WebSocketTransport] = set()  # Every connection not yet ended.

    @property
    def port(self) -> int:
        """The port the listener is bound to, read from its socket once started.

        Returns:
            The bound port; the OS-assigned one when 0 was requested.

        Raises:
            RuntimeError: ``start`` has not been called.
        """
        if self._server is None:
            raise RuntimeError("The WebSocket server has no port until start() has been called.")
        # websockets binds one socket per resolved address; a loopback host resolves to one.
        bound_port: int = self._server.sockets[0].getsockname()[1]
        return bound_port

    @property
    def uri(self) -> str:
        """The URI a client dials to reach this listener, ``ws://host:port``.

        Returns:
            The plaintext URI; an IPv6 host is bracketed as the URI grammar requires.

        Raises:
            RuntimeError: ``start`` has not been called.
        """
        host = f"[{self._host}]" if ":" in self._host else self._host
        return f"ws://{host}:{self.port}"

    async def start(self) -> None:
        """Bind the listener and begin accepting; a second call does nothing.

        Returns:
            None, once the socket is bound and listening.

        Raises:
            OSError: The address is in use or cannot be bound.
        """
        if self._server is not None:
            return
        # Bind and listen: instant on loopback. The keepalive, close and handshake bounds are
        # the same constants the client dials with, so both ends fail a dead link alike, and
        # max_size is the codec's limit so websockets refuses an oversized frame before the
        # codec sees it.
        self._server = await serve(
            self._accept,
            self._host,
            self._port,
            max_size=self._codec.max_frame_bytes,
            ping_interval=PING_INTERVAL_S,
            ping_timeout=PING_TIMEOUT_S,
            close_timeout=CLOSE_TIMEOUT_S,
            open_timeout=self._open_timeout_s,
        )

    async def connections(self) -> AsyncGenerator[WebSocketTransport, None]:
        """Yield each accepted connection as a transport until ``close`` is called.

        Returns:
            One WebSocketTransport per peer that completed the handshake, in accept order.
        """
        while True:
            # In-process queue: resolves when a peer connects or close() ends the listing; no
            # timeout applies because a Hive Stand with no callers is simply waiting.
            transport = await self._accepted.get()
            if transport is None:
                return
            yield transport

    async def close(self) -> None:
        """Close every open connection with 1000, stop listening and wait for every handler.

        Returns:
            None, once the listener socket is closed and every handler task has ended. A
            second call, or a call before ``start``, does nothing.
        """
        if self._server is None or self._closed:
            return
        self._closed = True
        # Our own close first, with 1000: websockets' Server.close would use 1001 (going away),
        # and 1000 is the code the spec gives a normal close; each peer's receive ends normally.
        for transport in list(self._open):
            await transport.close()
        self._server.close()
        # Waits for the listener and for every handler task websockets owns; each handler
        # returns when its connection's close completes, bounded by CLOSE_TIMEOUT_S.
        await self._server.wait_closed()
        self._accepted.put_nowait(None)

    async def _accept(self, connection: ServerConnection) -> None:
        """Handle one accepted connection: wrap, queue, and stay alive until it ends."""
        transport = WebSocketTransport(connection, self._codec)
        self._open.add(transport)
        self._accepted.put_nowait(transport)
        try:
            # Returning would make websockets close the connection, so the handler waits for
            # the connection to end: the consumer's close, the peer's disconnect, or close()
            # here. Unbounded on purpose; close() is what ends it.
            await connection.wait_closed()
        finally:
            self._open.discard(transport)
