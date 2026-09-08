"""Define the Transport protocol that carries Envelopes between two bees, and its implementations.

A Transport is one connection between two bees (the Queen, the central orchestrator; a Warden,
a per-Cell supervisor; a Worker, a subagent; a Pollen Packet, the thin gateway on an enrolled
device) that moves Envelopes (the outer wrapper every Waggle message travels in) as frames, in
order, at most once. Two implementations satisfy it: ``MemoryTransport``, an in-process pair for
tests and for the Warden and Workers that run inside the Queen's process, and the WebSocket
transport for anything that crosses a process or machine boundary, split into the connection
(``WebSocketTransport``), the dialling client (``WebSocketClientTransport``) and the listener
(``WebSocketServer``). Every check on a frame (size, shape, version, kind, signature) belongs to
the Codec; transports are policy-free carriers of bytes.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Called by every bee's loop to send and receive; calls into waggle.codec for every frame and
    into waggle.clock for the client's backoff sleeps. The outbox (waggle.outbox) replays through
    a Transport after a dropped link.

Key invariants:
    - Delivery at this layer is at-most-once, with ordering preserved within one connection;
      retries and durability belong to the caller through the outbox (spec section 9).
    - A frame that fails to decode closes the connection with the close code
      ``close_code_for`` maps it to, except InvalidPayloadError, after which ``receive`` may be
      called again on the same connection (spec section 7).
    - The memory transport moves bytes through the same Codec as the WebSocket one, so it is a
      faithful stand-in in the conformance suite that runs over both.

See Also:
    - docs/waggle/spec.md section 9 for the contract every implementation is held to.
    - docs/adr/0004-waggle-transport-websocket-json.md for the transport decision.
    - packages/waggle/tests/contracts/test_transport_contract.py for the conformance suite.

Public API:
    - Transport: the protocol (connect, send, receive, close, is_connected).
    - close_code_for, CLOSE_CODE_FOR and the CLOSE_* codes: the WebSocket close code a decode
      failure maps to, shared by both transports.
    - MemoryTransport: the in-process pair (pair, inject_frame, drop).
    - WebSocketTransport: one open websockets connection, client or server side.
    - WebSocketClientTransport: dials out with capped backoff through the injected Clock.
    - WebSocketServer: the loopback-by-default listener that hands out connections.
    - The commented constants behind them: PING_INTERVAL_S, PING_TIMEOUT_S, CLOSE_TIMEOUT_S,
      RECONNECT_INITIAL_S, RECONNECT_FACTOR, RECONNECT_MAX_S, OPEN_TIMEOUT_S,
      DEFAULT_MAX_ATTEMPTS, DEFAULT_HOST, OS_ASSIGNED_PORT.
"""

from waggle.transport.base import (
    CLOSE_CODE_FOR,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_NORMAL,
    CLOSE_POLICY_VIOLATION,
    CLOSE_PROTOCOL_ERROR,
    Transport,
    close_code_for,
)
from waggle.transport.memory import MemoryTransport
from waggle.transport.websocket import (
    CLOSE_TIMEOUT_S,
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    WebSocketTransport,
)
from waggle.transport.websocket_client import (
    DEFAULT_MAX_ATTEMPTS,
    OPEN_TIMEOUT_S,
    RECONNECT_FACTOR,
    RECONNECT_INITIAL_S,
    RECONNECT_MAX_S,
    WebSocketClientTransport,
)
from waggle.transport.websocket_server import DEFAULT_HOST, OS_ASSIGNED_PORT, WebSocketServer

__all__ = [
    "CLOSE_CODE_FOR",
    "CLOSE_MESSAGE_TOO_BIG",
    "CLOSE_NORMAL",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_PROTOCOL_ERROR",
    "CLOSE_TIMEOUT_S",
    "DEFAULT_HOST",
    "DEFAULT_MAX_ATTEMPTS",
    "OPEN_TIMEOUT_S",
    "OS_ASSIGNED_PORT",
    "PING_INTERVAL_S",
    "PING_TIMEOUT_S",
    "RECONNECT_FACTOR",
    "RECONNECT_INITIAL_S",
    "RECONNECT_MAX_S",
    "MemoryTransport",
    "Transport",
    "WebSocketClientTransport",
    "WebSocketServer",
    "WebSocketTransport",
    "close_code_for",
]
