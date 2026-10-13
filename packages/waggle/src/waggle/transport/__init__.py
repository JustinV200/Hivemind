"""Define the Transport protocol that carries Envelopes between two bees, and its implementations.

A Transport is one connection between two bees (the Queen, the central orchestrator; a Warden,
a per-Cell supervisor; a Worker, a subagent; a Pollen Packet, the thin gateway on an enrolled
device) that moves Envelopes (the outer wrapper every Waggle message travels in) as frames, in
order, at most once. Two implementations satisfy it: ``MemoryTransport``, an in-process pair for
tests and for the Warden and Workers that run inside the Queen's process, and the WebSocket
transport for anything that crosses a process or machine boundary, split into the connection
(``WebSocketTransport``), the dialling client (``WebSocketClientTransport``) and the listener
(``WebSocketServer``). Every check on a frame (size, shape, version, kind, signature) belongs to
the Codec; transports are policy-free carriers of bytes. The one exception is the listener's own
defence (phase 11): given ``ServerLimits`` it counts each connection's frames against a ceiling
and drops a duplicate envelope id seen again inside a replay window shared by all connections, and
it can serve ``wss://`` with a certificate a dialling node pins by fingerprint (``tls``).

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
    - A listener built without limits and without TLS behaves exactly as it always did; with
      them, a connection over its frame ceiling is closed with 4029 (``FrameCeilingError``) and a
      duplicate is dropped before any receiver sees it, each reported to the caller's callback.
    - A dial with a certificate pin never completes against another certificate, and a mismatch
      is never retried (``TlsPinMismatchError``).

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
    - WebSocketServer and ServerOptions: the loopback-by-default listener that hands out
      connections, and the typed keyword settings it takes (limits, ssl_context, on_refusal,
      clock, open_timeout_s).
    - ReceiveHooks: the two questions (frame_admitted, envelope_admitted) a WebSocketTransport
      asks of a listener's limits; ConnectionLimiter satisfies it.
    - ServerLimits, ServerLimiter, ConnectionLimiter, ConnectionRefusal, RefusalKind,
      RefusalHandler: a listener's limits, the objects that apply them, and the refusal it
      reports (kind REPLAY or FRAME_CEILING, with the node id and the envelope id).
    - ReplayWindow and ReplayLimits: the pure per-node window of envelope ids seen.
    - FrameCeiling and CeilingLimits: the pure token bucket behind the frame ceiling.
    - CLOSE_FRAME_CEILING: the close code (4029) a listener sends a connection over its ceiling.
    - server_ssl_context, pinned_client_ssl_context, certificate_sha256, is_valid_pin,
      peer_certificate_sha256, check_pin (waggle.transport.tls): the listener's TLS context, the
      client's pin-only context and the fingerprint check behind ``DialOptions.tls_pin_sha256``.
    - The commented constants behind them: PING_INTERVAL_S, PING_TIMEOUT_S, CLOSE_TIMEOUT_S,
      RECONNECT_INITIAL_S, RECONNECT_FACTOR, RECONNECT_MAX_S, OPEN_TIMEOUT_S,
      DEFAULT_MAX_ATTEMPTS, DEFAULT_HOST, OS_ASSIGNED_PORT, REPLAY_WINDOW_S, MAX_REMEMBERED_IDS,
      MAX_FRAMES_PER_MINUTE, BURST_FRAMES, REFUSAL_REPORT_TIMEOUT_S, PIN_HEX_CHARS, ABORT_WAIT_S.
"""

from waggle.transport.base import (
    CLOSE_CODE_FOR,
    CLOSE_FRAME_CEILING,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_NORMAL,
    CLOSE_POLICY_VIOLATION,
    CLOSE_PROTOCOL_ERROR,
    Transport,
    close_code_for,
)
from waggle.transport.ceiling import (
    BURST_FRAMES,
    MAX_FRAMES_PER_MINUTE,
    CeilingLimits,
    FrameCeiling,
)
from waggle.transport.limits import (
    REFUSAL_REPORT_TIMEOUT_S,
    ConnectionLimiter,
    ConnectionRefusal,
    RefusalHandler,
    RefusalKind,
    ServerLimiter,
    ServerLimits,
)
from waggle.transport.memory import MemoryTransport
from waggle.transport.replay import (
    MAX_REMEMBERED_IDS,
    REPLAY_WINDOW_S,
    ReplayLimits,
    ReplayWindow,
)
from waggle.transport.tls import (
    ABORT_WAIT_S,
    PIN_HEX_CHARS,
    certificate_sha256,
    check_pin,
    is_valid_pin,
    peer_certificate_sha256,
    pinned_client_ssl_context,
    server_ssl_context,
)
from waggle.transport.websocket import (
    CLOSE_TIMEOUT_S,
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    ReceiveHooks,
    WebSocketTransport,
)
from waggle.transport.websocket_client import (
    DEFAULT_MAX_ATTEMPTS,
    OPEN_TIMEOUT_S,
    RECONNECT_FACTOR,
    RECONNECT_INITIAL_S,
    RECONNECT_MAX_S,
    DialOptions,
    WebSocketClientTransport,
)
from waggle.transport.websocket_server import (
    DEFAULT_HOST,
    OS_ASSIGNED_PORT,
    ServerOptions,
    WebSocketServer,
)

__all__ = [
    "ABORT_WAIT_S",
    "BURST_FRAMES",
    "CLOSE_CODE_FOR",
    "CLOSE_FRAME_CEILING",
    "CLOSE_MESSAGE_TOO_BIG",
    "CLOSE_NORMAL",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_PROTOCOL_ERROR",
    "CLOSE_TIMEOUT_S",
    "DEFAULT_HOST",
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_FRAMES_PER_MINUTE",
    "MAX_REMEMBERED_IDS",
    "OPEN_TIMEOUT_S",
    "OS_ASSIGNED_PORT",
    "PING_INTERVAL_S",
    "PING_TIMEOUT_S",
    "PIN_HEX_CHARS",
    "RECONNECT_FACTOR",
    "RECONNECT_INITIAL_S",
    "RECONNECT_MAX_S",
    "REFUSAL_REPORT_TIMEOUT_S",
    "REPLAY_WINDOW_S",
    "CeilingLimits",
    "ConnectionLimiter",
    "ConnectionRefusal",
    "DialOptions",
    "FrameCeiling",
    "MemoryTransport",
    "ReceiveHooks",
    "RefusalHandler",
    "RefusalKind",
    "ReplayLimits",
    "ReplayWindow",
    "ServerLimiter",
    "ServerLimits",
    "ServerOptions",
    "Transport",
    "WebSocketClientTransport",
    "WebSocketServer",
    "WebSocketTransport",
    "certificate_sha256",
    "check_pin",
    "close_code_for",
    "is_valid_pin",
    "peer_certificate_sha256",
    "pinned_client_ssl_context",
    "server_ssl_context",
]
