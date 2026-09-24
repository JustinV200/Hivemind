"""Hold the Entrance's live streams: the trail follower, the sockets, and one view per stream.

Clients of the Hive Entrance never poll (codingrules 8.11, ADR-0032): every live view is a WebSocket
fed from durable state. ``hub`` is the ``StreamHub`` that follows the Pheromone Trail once and fans
its events out to bounded per-subscriber queues, closing a subscriber that falls behind;
``registry`` keeps every live socket so each closes the moment its session ends (and is the Entrance
Reducer's ``StreamCloser``); ``socket`` is the lifecycle every view shares (the first frame
authenticates within five seconds, then the view races the client, the registry and a session
watchdog); ``views`` holds the views built so far (``/v1/chat/stream``, ``/v1/push/stream``,
``/v1/entrance/stream``) as ``VIEWS``; ``orders`` is the follower that obeys a Guard Bee's
``guard.reduce_ordered`` (ADR-0035); ``errors`` names why a stream closes.

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
    - StreamHub, StreamSubscription, EventFilter, DEFAULT_BACKLOG: the follower and its queues
      (hub).
    - SocketRegistry, LiveSocket: every live socket, closable by session, device or listener
      (registry).
    - serve_socket, send_frame, StreamContext, View, SOCKET_RECHECK_S: the shared lifecycle
      (socket).
    - VIEWS, CHAT_RESYNC_S, chat_stream, push_stream, security_stream: the views (views).
    - ReduceOrderFollower, REDUCE_ORDERED_KIND, ORDER_ACTOR: the Guard's reduce orders (orders).
    - CloseReason, StreamClosedError: why a stream closed (errors).
"""

from hivemind.entrance.streams.errors import CloseReason, StreamClosedError
from hivemind.entrance.streams.hub import (
    DEFAULT_BACKLOG,
    EventFilter,
    StreamHub,
    StreamSubscription,
)
from hivemind.entrance.streams.orders import (
    ORDER_ACTOR,
    REDUCE_ORDERED_KIND,
    ReduceOrderFollower,
)
from hivemind.entrance.streams.registry import LiveSocket, SocketRegistry
from hivemind.entrance.streams.socket import (
    SOCKET_RECHECK_S,
    StreamContext,
    View,
    send_frame,
    serve_socket,
)
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
    "EventFilter",
    "LiveSocket",
    "ReduceOrderFollower",
    "SocketRegistry",
    "StreamClosedError",
    "StreamContext",
    "StreamHub",
    "StreamSubscription",
    "View",
    "chat_stream",
    "push_stream",
    "security_stream",
    "send_frame",
    "serve_socket",
]
