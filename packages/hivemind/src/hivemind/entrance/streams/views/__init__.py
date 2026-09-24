"""Hold the live views: one WebSocket stream per view, each declaring its access like a route.

Clients never poll (codingrules 8.11, ADR-0032): every live view of the Hive Entrance is a
WebSocket fed from durable state, declared as a ``SocketSpec`` (its path, listeners and access,
checked on its first frame exactly as a route's) and served through the shared lifecycle in
``hivemind.entrance.streams.socket``. ``landing`` holds the Landing Board's own traffic (the chat,
the push channel, the Entrance's security events); the Hive's views each have a module: ``trail``
(every trail event), ``telemetry`` (every bee's telemetry from the Queen's Heartbeats, the one
view not fed by the trail), ``forage`` (Forage ledger deltas), ``tasks`` (task-graph deltas per
principal), ``episodes`` (new episode records) and ``cells`` (Cell status). ``pump`` is the loop
they share. ``registry``'s ``VIEWS`` joins them all into the route table, and the Landing Board's
OpenAPI document lists each one under ``x-hive-streams``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. ``VIEWS``
    joins the route table in ``hivemind.entrance.app``. Calls into the stream hub, the telemetry
    board, the read side and the view models.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every view subscribes with the Entrance's backlog, so a reader that lags is closed with
      FELL_BEHIND and never slows the feed.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "One stream per view".

Public API:
    - VIEWS: every view's row, in the order the document lists them (registry).
    - CHAT_RESYNC_S, chat_stream, push_stream, security_stream, LANDING_VIEWS, live_sender: the
      Landing Board's own views, and the live push sender that detaches a socket its client has
      left (landing).
    - TRAIL_VIEW, trail_stream; TELEMETRY_VIEW, telemetry_stream; FORAGE_VIEW, forage_stream;
      TASKS_VIEW, task_stream, QUEEN_PRINCIPAL; EPISODES_VIEW, episode_stream; CELLS_VIEW,
      cell_stream: the Hive's views (trail, telemetry, forage, tasks, episodes, cells).
    - pump, trail_subscription, telemetry_subscription, Frames: the shared loop (pump).
"""

from hivemind.entrance.streams.views.cells import CELLS_VIEW, cell_stream
from hivemind.entrance.streams.views.episodes import EPISODES_VIEW, episode_stream
from hivemind.entrance.streams.views.forage import FORAGE_VIEW, forage_stream
from hivemind.entrance.streams.views.landing import (
    CHAT_RESYNC_S,
    LANDING_VIEWS,
    chat_stream,
    live_sender,
    push_stream,
    security_stream,
)
from hivemind.entrance.streams.views.pump import (
    Frames,
    pump,
    telemetry_subscription,
    trail_subscription,
)
from hivemind.entrance.streams.views.registry import VIEWS
from hivemind.entrance.streams.views.tasks import QUEEN_PRINCIPAL, TASKS_VIEW, task_stream
from hivemind.entrance.streams.views.telemetry import TELEMETRY_VIEW, telemetry_stream
from hivemind.entrance.streams.views.trail import TRAIL_VIEW, trail_stream

__all__ = [
    "CELLS_VIEW",
    "CHAT_RESYNC_S",
    "EPISODES_VIEW",
    "FORAGE_VIEW",
    "LANDING_VIEWS",
    "QUEEN_PRINCIPAL",
    "TASKS_VIEW",
    "TELEMETRY_VIEW",
    "TRAIL_VIEW",
    "VIEWS",
    "Frames",
    "cell_stream",
    "chat_stream",
    "episode_stream",
    "forage_stream",
    "live_sender",
    "pump",
    "push_stream",
    "security_stream",
    "task_stream",
    "telemetry_stream",
    "telemetry_subscription",
    "trail_stream",
    "trail_subscription",
]
