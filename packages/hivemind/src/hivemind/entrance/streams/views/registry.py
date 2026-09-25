"""Register every live view: adding a stream to the Landing Board is one line here.

The Hive Entrance builds both applications, and the Landing Board's OpenAPI document, from one
table (ADR-0040). ``VIEWS`` is its WebSocket half, as ``hivemind.entrance.routes.RESOURCE_ROUTES``
is its HTTP half: each view module declares its own ``SocketSpec`` and joins by being named once
below; ``hivemind.entrance.app`` joins the two into the ``RouteTable``, and the document lists the
views in this order under ``x-hive-streams``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Read
    by ``hivemind.entrance.app``. Calls into each view module for its row only.

Key invariants:
    - Every view appears once.

See Also:
    - hivemind.entrance.gate.spec for what a SocketSpec declares.
"""

from __future__ import annotations

from hivemind.entrance.gate.spec import SocketSpec
from hivemind.entrance.streams.views.cells import CELLS_VIEW
from hivemind.entrance.streams.views.episodes import EPISODES_VIEW
from hivemind.entrance.streams.views.forage import FORAGE_VIEW
from hivemind.entrance.streams.views.landing import LANDING_VIEWS
from hivemind.entrance.streams.views.tasks import TASKS_VIEW
from hivemind.entrance.streams.views.telemetry import TELEMETRY_VIEW
from hivemind.entrance.streams.views.trail import TRAIL_VIEW

__all__ = ["VIEWS"]

# The Landing Board's own views first (chat, push, security), then the Hive's.
VIEWS: tuple[SocketSpec, ...] = (
    *LANDING_VIEWS,
    TRAIL_VIEW,
    TELEMETRY_VIEW,
    FORAGE_VIEW,
    TASKS_VIEW,
    EPISODES_VIEW,
    CELLS_VIEW,
)
