"""Define bind_sticky: start the Cell listener on the port it had, when the manifest names none.

A Virtual Cell dials the Queen at the URL it was provisioned with, and keeps dialing it when her
process dies (ADR-0062). With `[virtual_cells] listen_port = 0`, the default, the OS chose that
port, and a restarted Queen would be handed another one, so no Cell that outlived her could find
her. `bind_sticky` makes the OS's first choice stick: it binds the port the listener had last
time (`ListenerPorts`, kept in the Queen's Requeening store), falls back to a fresh choice when
that port is taken, and remembers whatever it bound. A port the manifest names is bound as named,
and nothing is remembered for it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Called
    by `hivemind.queen.cell_gate.listener.CellListener.start`. Calls into waggle (the server)
    only; `ListenerPorts` is satisfied by `hivemind.queen.requeening.RequeeningStore`.

Key invariants:
    - A configured port is never replaced: only an OS-chosen one is recalled or remembered.
    - A recalled port that cannot be bound never stops the Queen: the OS chooses again.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md.
    - hivemind.queen.requeening.store for where the port is kept.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from hivemind.common.logging import get_logger
from waggle.transport.websocket_server import OS_ASSIGNED_PORT, WebSocketServer

PORT_NAME = "virtual_cells"  # The key the Cell listener's port is kept under.

log = get_logger(__name__)

__all__ = ["PORT_NAME", "ListenerPorts", "bind_sticky"]


class ListenerPorts(Protocol):
    """Recall and remember the port a named listener bound last."""

    async def recall_port(self, name: str) -> int | None:
        """Return the port `name` bound last, or None.

        Args:
            name: The listener's name.
        """
        ...

    async def remember_port(self, name: str, port: int) -> None:
        """Record that `name` bound `port`.

        Args:
            name: The listener's name.
            port: The port it bound.
        """
        ...


async def bind_sticky(
    make: Callable[[int], WebSocketServer], configured: int, ports: ListenerPorts | None
) -> WebSocketServer:
    """Start a server on `configured`, or on the remembered port when the OS is to choose.

    Args:
        make: Builds the (not yet started) server for one port.
        configured: The manifest's port; `OS_ASSIGNED_PORT` lets the OS choose.
        ports: Where the last OS-chosen port is kept; None keeps nothing.

    Returns:
        The started server.

    Raises:
        OSError: The configured port (or, failing the remembered one, any port) cannot be bound.
    """
    if configured != OS_ASSIGNED_PORT or ports is None:
        return await _started(make(configured))  # Named in the manifest, or nowhere to keep it.
    # Latency: one local SQLite read.
    recalled = await ports.recall_port(PORT_NAME)
    server: WebSocketServer | None = None
    if recalled is not None:
        try:
            server = await _started(make(recalled))
        except OSError:
            # Taken by something else since: the OS chooses again, and the Cells that dial the
            # old port are left to the Undertaker; nothing here stops the Queen.
            log.warning("cell_gate.port_taken", port=recalled)
    if server is None:
        server = await _started(make(OS_ASSIGNED_PORT))
    await ports.remember_port(PORT_NAME, server.port)
    return server


async def _started(server: WebSocketServer) -> WebSocketServer:
    """Start `server` (an external bind, immediate or an OSError) and return it."""
    await server.start()
    return server
