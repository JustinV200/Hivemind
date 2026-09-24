"""Hold the Entrance's live streams: the trail follower, the sockets, and one view per stream.

Clients of the Hive Entrance never poll (codingrules 8.11, ADR-0032): every live view is a WebSocket
fed from durable state. ``hub`` is the ``StreamHub`` that follows the Pheromone Trail once and fans
its events out to bounded per-subscriber queues (``subscription``), closing a subscriber that falls
behind; ``telemetry`` is the board the Queen's Heartbeats reach through the hook the composition
root wires (a Heartbeat never reaches the trail), fanned out the same way; ``registry`` keeps every
live socket so each closes the moment its session ends (and is the Entrance Reducer's
``StreamCloser``); ``socket`` is the lifecycle every view shares (the first frame authenticates
within the deadline, five seconds, then the view races the client, the registry and a session
watchdog); ``views`` holds every view (the chat, push and security views, and the Hive's trail,
telemetry, Forage, task-graph, episode and Cell-status views) as ``VIEWS``; ``orders`` is the
follower that obeys a Guard Bee's ``guard.reduce_ordered`` (ADR-0035); ``errors`` names why a
stream closes.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Run by the Entrance
    runtime (the hub and the follower) and mounted by ``hivemind.entrance.app`` (the views). Calls
    into ``hivemind.pheromone``, the gate, the Queen's chat log and the live push hub.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - The trail is followed once, however many views are open.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "One stream per view".

Public API:
    - StreamHub, EventFilter: the trail follower (hub).
    - StreamSubscription, DEFAULT_BACKLOG: a subscriber's bounded queue (subscription).
    - TelemetryBoard, TelemetrySample: the Queen's Heartbeats, kept and fanned out (telemetry).
    - SocketRegistry, LiveSocket: every live socket, closable by session, device or listener
      (registry).
    - serve_socket, send_frame, StreamContext, View, Duplex, SOCKET_RECHECK_S: the shared
      lifecycle (socket).
    - VIEWS, CHAT_RESYNC_S, chat_stream, push_stream, security_stream: the views (views; each
      Hive view is reached through ``hivemind.entrance.streams.views``).
    - ReduceOrderFollower, REDUCE_ORDERED_KIND, ORDER_ACTOR: the Guard's reduce orders (orders).
    - CloseReason, StreamClosedError: why a stream closed (errors).
"""

from hivemind.entrance.streams.errors import CloseReason, StreamClosedError
from hivemind.entrance.streams.hub import EventFilter, StreamHub
from hivemind.entrance.streams.orders import (
    ORDER_ACTOR,
    REDUCE_ORDERED_KIND,
    ReduceOrderFollower,
)
from hivemind.entrance.streams.registry import LiveSocket, SocketRegistry
from hivemind.entrance.streams.socket import (
    SOCKET_RECHECK_S,
    Duplex,
    StreamContext,
    View,
    send_frame,
    serve_socket,
)
from hivemind.entrance.streams.subscription import DEFAULT_BACKLOG, StreamSubscription
from hivemind.entrance.streams.telemetry import TelemetryBoard, TelemetrySample
from hivemind.entrance.streams.views import (
    CHAT_RESYNC_S,
    VIEWS,
    chat_stream,
    push_stream,
    security_stream,
)

__all__ = [
    "CHAT_RESYNC_S",
    "DEFAULT_BACKLOG",
    "ORDER_ACTOR",
    "REDUCE_ORDERED_KIND",
    "SOCKET_RECHECK_S",
    "VIEWS",
    "CloseReason",
    "Duplex",
    "EventFilter",
    "LiveSocket",
    "ReduceOrderFollower",
    "SocketRegistry",
    "StreamClosedError",
    "StreamContext",
    "StreamHub",
    "StreamSubscription",
    "TelemetryBoard",
    "TelemetrySample",
    "View",
    "chat_stream",
    "push_stream",
    "security_stream",
    "send_frame",
    "serve_socket",
]
