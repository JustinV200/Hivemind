"""Tests for hivemind.queen.cell_gate.port: the Cell listener's OS-chosen port sticks.

A restarted Queen whose manifest lets the OS choose binds the port she had, so a Cell that
outlived her finds her again; when that port has been taken meanwhile she binds a fresh one and
remembers it. A port the manifest names is bound as named and never remembered.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/port.py (codingrules section 3), over real loopback
    sockets.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.port for the module under test.
"""

from __future__ import annotations

import socket

from hivemind.queen.cell_gate.port import PORT_NAME, bind_sticky
from hivemind.queen.requeening import MemoryRequeeningStore
from waggle.codec import Codec
from waggle.transport.websocket_server import OS_ASSIGNED_PORT, WebSocketServer


def _make(port: int) -> WebSocketServer:
    """A loopback server on `port`, not yet started."""
    return WebSocketServer(Codec(), port=port)


async def test_a_restarted_listener_binds_the_port_it_had_and_a_fresh_one_if_it_is_taken() -> None:
    ports = MemoryRequeeningStore()
    first = await bind_sticky(_make, OS_ASSIGNED_PORT, ports)
    had = first.port
    await first.close()  # The Queen's process dies; her port is free again.

    again = await bind_sticky(_make, OS_ASSIGNED_PORT, ports)
    same = again.port
    await again.close()
    # Something else takes the port while she is down the second time.
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", same))
        squatter.listen()
        fresh = await bind_sticky(_make, OS_ASSIGNED_PORT, ports)
        fresh_port = fresh.port
        await fresh.close()

    assert same == had
    assert fresh_port != had
    assert await ports.recall_port(PORT_NAME) == fresh_port


async def test_a_configured_port_is_bound_as_named_and_never_remembered() -> None:
    ports = MemoryRequeeningStore()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        configured = probe.getsockname()[1]

    server = await bind_sticky(_make, configured, ports)
    bound = server.port
    await server.close()

    assert bound == configured
    assert await ports.recall_port(PORT_NAME) is None
