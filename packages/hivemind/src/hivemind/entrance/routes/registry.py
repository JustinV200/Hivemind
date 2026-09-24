"""Register every resource's routes: adding a resource to the Landing Board is one line here.

The Hive Entrance builds both of its applications, and the Landing Board's OpenAPI document, from
one route table (ADR-0032). ``RESOURCE_ROUTES`` is the HTTP half of it: each resource module (one
per resource under ``/v1/``) declares its own rows, and joins the Landing Board by being named once
below. The WebSocket half is ``hivemind.entrance.streams.VIEWS``; ``hivemind.entrance.app`` joins
the two into the ``RouteTable``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Read by
    ``hivemind.entrance.app``. Calls into each resource module for its rows only.

Key invariants:
    - Every resource appears once; the document lists its rows in this order.

See Also:
    - hivemind.entrance.gate.spec for what a row declares.
"""

from __future__ import annotations

from hivemind.entrance.gate.spec import RouteSpec
from hivemind.entrance.routes import auth, chat, devices, enrol, goals, inbox, push
from hivemind.entrance.routes.entrance import DOOR_ROUTES, ENROLMENT_ROUTES, HELD_ROUTES
from hivemind.entrance.routes.hive import (
    CELL_ROUTES,
    EPISODE_ROUTES,
    FORAGE_ROUTES,
    LLM_ROUTES,
    TASK_ROUTES,
    TRAIL_ROUTES,
    WARDEN_ROUTES,
)
from hivemind.entrance.routes.later import HONEY_ROUTES, SWARM_ROUTES, TOOL_ROUTES

__all__ = ["RESOURCE_ROUTES"]

# One entry per resource: the rows it serves, as its module declares them.
RESOURCE_ROUTES: tuple[tuple[RouteSpec, ...], ...] = (
    auth.ROUTES,
    enrol.ROUTES,
    DOOR_ROUTES,
    ENROLMENT_ROUTES,
    HELD_ROUTES,
    devices.ROUTES,
    goals.ROUTES,
    chat.ROUTES,
    inbox.ROUTES,
    push.ROUTES,
    TASK_ROUTES,
    CELL_ROUTES,
    WARDEN_ROUTES,
    FORAGE_ROUTES,
    EPISODE_ROUTES,
    TRAIL_ROUTES,
    LLM_ROUTES,
    TOOL_ROUTES,
    HONEY_ROUTES,
    SWARM_ROUTES,
)
