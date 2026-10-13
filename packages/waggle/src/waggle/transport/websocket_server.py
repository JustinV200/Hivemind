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
by the tier's VPN (spec section 6) or serves ``wss://`` itself, given an ``ssl_context`` (see
``waggle.transport.tls``), whose certificate a dialling node pins instead of trusting a CA.

Phase 11 (roadmap steps 11.3a and 11.3b) adds the listener's own defences. Given ``limits``, each
accepted connection counts its frames against a ceiling and every decoded envelope is checked
against one replay window shared by all connections (``waggle.transport.limits``); a duplicate
is dropped and a flooding connection is closed with 4029, each reported to ``on_refusal`` so the
Hive Stand's listener wrapper, not waggle, writes the trail. Without ``limits`` and
``ssl_context`` the server behaves exactly as before. The optional settings travel as keyword
arguments typed by ``ServerOptions`` so the constructor stays within the five-parameter limit.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Started by the Hive Stand's composition root; yields
    waggle.transport.websocket.WebSocketTransport to the Queen's accept loop; calls into
    waggle.codec, waggle.transport.websocket, waggle.transport.limits and the websockets library.

Key invariants:
    - Every connection accepted is either yielded by connections() or closed by close(); none
      is leaked, and every handler task ends before close() returns.
    - The listener passes the codec's max_frame_bytes as websockets' max_size, so an oversized
      frame is refused with 1009 before it reaches the codec.
    - start() and close() are idempotent; a closed server is not restarted (make a new one).
    - One replay window per server, shared by every connection it accepts; one frame ceiling per
      connection. A server built without limits wraps its connections with no hooks at all.
    - ``uri`` is ``wss://`` exactly when an ssl_context was given.

See Also:
    - waggle.transport.websocket for the transport each accepted connection becomes.
    - waggle.transport.websocket_client for the dial every remote bee makes to this listener.
    - waggle.transport.limits and waggle.transport.tls for the limits and the TLS pieces.
    - docs/adr/0004-waggle-transport-websocket-json.md for the one-listener decision.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncGenerator
from typing import TypedDict, Unpack

from websockets.asyncio.server import Server, ServerConnection, serve

from waggle.clock import Clock, SystemClock
from waggle.codec import Codec
from waggle.transport.limits import RefusalHandler, ServerLimiter, ServerLimits
from waggle.transport.websocket import (
    CLOSE_TIMEOUT_S,
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    WebSocketTransport,
)

DEFAULT_HOST = "127.0.0.1"  # Loopback: nothing listens off the machine until a tier fronts it.
OS_ASSIGNED_PORT = 0  # Let the OS pick a free port; read it back from the port property.
OPEN_TIMEOUT_S = 10.0  # Real time a peer has to finish the handshake (websockets' default).

__all__ = ["DEFAULT_HOST", "OPEN_TIMEOUT_S", "OS_ASSIGNED_PORT", "ServerOptions", "WebSocketServer"]


class ServerOptions(TypedDict, total=False):
    """The optional keyword settings of a WebSocketServer; every key may be left out.

    A TypedDict rather than constructor parameters so the listener's growing list of settings
    stays one typed group (codingrules 5.1: a long parameter list becomes an argument group):
    ``WebSocketServer(codec, port=0, limits=ServerLimits(), ssl_context=context)`` type-checks
    each key and rejects an unknown one.

    Attributes:
        open_timeout_s: Real seconds a peer has to complete the handshake, TLS included;
            OPEN_TIMEOUT_S by default.
        limits: The per-connection frame ceiling and the shared replay window to enforce; None
            (the default) enforces neither and the server is exactly the plain listener.
        ssl_context: Serve ``wss://`` with this context (``waggle.transport.tls.
            server_ssl_context`` builds one from a certificate and key); None serves ``ws://``.
        on_refusal: Awaited with a ConnectionRefusal each time ``limits`` refuses a frame
            flood or a duplicate, before the connection is closed or the duplicate dropped. It
            must be quick and must not raise; None records nothing.
        clock: The injected Clock the limits read time from; a SystemClock by default.
    """

    open_timeout_s: float
    limits: ServerLimits | None
    ssl_context: ssl.SSLContext | None
    on_refusal: RefusalHandler | None
    clock: Clock


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
        **options: Unpack[ServerOptions],
    ) -> None:
        """Remember where to listen and how; nothing is bound until ``start``.

        Args:
            codec: Given to every accepted transport; its limit is the listener's max_size.
            host: The interface to bind; loopback by default.
            port: The port to bind; 0 lets the OS choose one.
            **options: Any of the ``ServerOptions`` keys: ``open_timeout_s`` (the handshake
                bound), ``limits`` (the frame ceiling and replay window), ``ssl_context`` (serve
                ``wss://``), ``on_refusal`` (hear of each refusal) and ``clock``.

        Raises:
            TypeError: A keyword that is not a ``ServerOptions`` key was given.
        """
        _refuse_unknown_options(options)
        self._codec = codec
        self._host = host
        self._port = port
        self._open_timeout_s = options.get("open_timeout_s", OPEN_TIMEOUT_S)
        self._ssl_context = options.get("ssl_context")
        self._limiter = _build_limiter(options)
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
        """The URI a client dials to reach this listener, ``ws://host:port`` or ``wss://``.

        Returns:
            ``wss://`` when an ssl_context was given, else the plaintext ``ws://`` form; an
            IPv6 host is bracketed as the URI grammar requires.

        Raises:
            RuntimeError: ``start`` has not been called.
        """
        scheme = "ws" if self._ssl_context is None else "wss"
        host = f"[{self._host}]" if ":" in self._host else self._host
        return f"{scheme}://{host}:{self.port}"

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
        # codec sees it. With an ssl_context the TLS handshake runs before the WebSocket one,
        # bounded by the same open_timeout (websockets reuses it as the TLS handshake bound).
        self._server = await serve(
            self._accept,
            self._host,
            self._port,
            max_size=self._codec.max_frame_bytes,
            ping_interval=PING_INTERVAL_S,
            ping_timeout=PING_TIMEOUT_S,
            close_timeout=CLOSE_TIMEOUT_S,
            open_timeout=self._open_timeout_s,
            ssl=self._ssl_context,
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
        # Each connection gets its own limiter (its own full frame bucket) over the server's one
        # shared replay window; None when the server was built without limits.
        hooks = None if self._limiter is None else self._limiter.for_connection()
        transport = WebSocketTransport(connection, self._codec, hooks)
        self._open.add(transport)
        self._accepted.put_nowait(transport)
        try:
            # Returning would make websockets close the connection, so the handler waits for
            # the connection to end: the consumer's close, the peer's disconnect, or close()
            # here. Unbounded on purpose; close() is what ends it.
            await connection.wait_closed()
        finally:
            self._open.discard(transport)


def _refuse_unknown_options(options: ServerOptions) -> None:
    """Raise TypeError for a keyword that is no ServerOptions key, as a real parameter would."""
    unknown = sorted(set(options) - ServerOptions.__optional_keys__)
    # The type checker rejects a misspelt key in typed code, but a mistyped ``ssl_context``
    # that was silently ignored would serve plaintext where TLS was meant, so untyped callers
    # (a script, a REPL) get the same refusal at run time.
    if unknown:
        raise TypeError(
            f"WebSocketServer got unexpected keyword argument(s) {', '.join(unknown)}; "
            f"the options are {', '.join(sorted(ServerOptions.__optional_keys__))}."
        )


def _build_limiter(options: ServerOptions) -> ServerLimiter | None:
    """Make the listener-wide limiter the options ask for, or None when no limits were given."""
    limits = options.get("limits")
    # No limits means no limiter at all, so a plain server pays nothing and its connections are
    # wrapped with no hooks, byte for byte as before phase 11.
    if limits is None:
        return None
    clock = options.get("clock")
    return ServerLimiter(
        limits, SystemClock() if clock is None else clock, options.get("on_refusal")
    )
