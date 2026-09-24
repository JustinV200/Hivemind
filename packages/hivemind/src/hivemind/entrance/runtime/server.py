"""Run one listener: a uvicorn server on a socket the Entrance bound, inside the Hive's own loop.

Both of the Hive Entrance's listeners are uvicorn servers running in the same event loop as the
Queen (ADR-0032), each on a socket the Entrance binds itself, so a loopback listener that cannot
bind is known before anything starts, and a port the operating system chose (tests) is known
before the application's Host check is built. uvicorn's own signal handling is off (``hive serve``
owns SIGINT and SIGTERM, and a listener must never exit the process), its lifespan is off, it
trusts no proxy header, WebSockets use the ``websockets`` sans-I/O implementation (the others raise
deprecation warnings), and a graceful shutdown is bounded to one second (the Entrance Reducer's
bound for closing the door).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Used by the
    Entrance's listeners. Calls into uvicorn and the standard library's socket and ssl modules.

Key invariants:
    - ``stop`` returns within about ``STOP_TIMEOUT_S``: graceful for one second, then forced.
    - A server never installs a signal handler.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "Two listeners are two
      applications built from one route table".
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
from collections.abc import Iterator

import uvicorn
from starlette.types import ASGIApp

from hivemind.common.logging import get_logger

GRACEFUL_SHUTDOWN_S = 1  # ADR-0032: a listener's graceful shutdown is bounded to one second.
STOP_TIMEOUT_S = 2.0  # The graceful second, uvicorn's own ticks, then a forced exit.
LISTEN_BACKLOG = 128  # Pending connections a listener queues; a Hive has few clients.
WEBSOCKET_MAX_BYTES = 1_048_576  # A frame larger than 1 MiB is refused: frames here are small.
LOOPBACK_NAME = "localhost"  # The one loopback name [entrance] bind may use besides an address.
_LOOPBACK_ADDRESS = "127.0.0.1"  # What localhost is bound as: never resolved through DNS.

log = get_logger(__name__)

__all__ = [
    "GRACEFUL_SHUTDOWN_S",
    "LOOPBACK_NAME",
    "STOP_TIMEOUT_S",
    "ListenerServer",
    "bind_listener",
]


def bind_listener(host: str, port: int) -> socket.socket:
    """Bind a listening TCP socket on ``host``:``port`` (port 0 lets the system choose).

    Args:
        host: An IP address, or ``localhost`` (bound as 127.0.0.1, never resolved).
        port: The port; 0 for any free one.

    Returns:
        The bound socket; uvicorn starts listening on it.

    Raises:
        OSError: The address cannot be bound (in use, not assigned to this host, not allowed).
    """
    address = _LOOPBACK_ADDRESS if host == LOOPBACK_NAME else host
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(LISTEN_BACKLOG)
    except OSError:
        sock.close()
        raise
    sock.setblocking(False)
    return sock


class ListenerServer:
    """One uvicorn server serving one application on one already-bound socket."""

    def __init__(
        self, app: ASGIApp, sock: socket.socket, tls: ssl.SSLContext | None = None
    ) -> None:
        """Configure the server; nothing is served until ``serve``.

        Args:
            app: The listener's application, already wrapped in the gate's checks.
            sock: The bound socket; the server closes it when it stops.
            tls: The TLS context to serve with (the remote listener), or None.
        """
        self._sock = sock
        self._port = int(sock.getsockname()[1])
        config = uvicorn.Config(
            app,
            lifespan="off",
            http="h11",
            ws="websockets-sansio",
            ws_max_size=WEBSOCKET_MAX_BYTES,
            proxy_headers=False,
            server_header=False,
            access_log=False,
            log_config=None,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
        )
        config.load()
        # Set after load(), which would otherwise build a context of its own from file paths.
        config.ssl = tls
        self._server = _QuietServer(config)
        self._finished = asyncio.Event()

    @property
    def port(self) -> int:
        """The port the socket is bound to."""
        return self._port

    @property
    def serving(self) -> bool:
        """Whether the server started and has not finished."""
        return self._server.started and not self._finished.is_set()

    async def serve(self) -> None:
        """Serve until ``stop``; an OSError from the socket propagates to the caller.

        Raises:
            OSError: The socket failed under the server.
        """
        try:
            await self._server.serve(sockets=[self._sock])
        finally:
            self._finished.set()
            log.info("entrance.listener_finished", port=self._port)

    async def stop(self) -> None:
        """Stop gracefully within one second, then force; idempotent."""
        self._server.should_exit = True
        # A server that never started has nothing to drain.
        if not self._server.started and not self._finished.is_set():
            self._sock.close()
            return
        try:
            # External wait: uvicorn's shutdown, bounded by its own graceful timeout.
            async with asyncio.timeout(STOP_TIMEOUT_S):
                await self._finished.wait()
        except TimeoutError:
            self._server.force_exit = True
            log.warning("entrance.listener_forced", port=self._port)


class _QuietServer(uvicorn.Server):
    """A uvicorn server that never installs signal handlers: ``hive serve`` owns the signals."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        """Install nothing; SIGINT and SIGTERM stay the composition root's."""
        yield
