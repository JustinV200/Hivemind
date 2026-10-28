"""Register every resource's routes: adding a resource to the Landing Board is one line here.

The Hive Entrance builds both of its applications, and the Landing Board's OpenAPI document, from
one route table (ADR-0040). ``RESOURCE_ROUTES`` is the HTTP half of it: each resource module (one
per resource under ``/v1/``) declares its own rows, and joins the Landing Board by being named once
below. The WebSocket half is ``hivemind.entrance.streams.VIEWS``; ``hivemind.entrance.app`` joins
the two into the ``RouteTable``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Read by
    ``hivemind.entrance.app``. Calls into each resource module for its rows only (the voice
    route's rows live with the rest of voice, in ``hivemind.entrance.voice``, and the image
    route's in ``hivemind.entrance.images``).

Key invariants:
    - Every resource appears once; the document lists its rows in this order.

See Also:
    - hivemind.entrance.gate.spec for what a row declares.
"""

from __future__ import annotations

from hivemind.entrance.gate.spec import RouteSpec
from hivemind.entrance.images import IMAGE_ROUTES
from hivemind.entrance.routes import auth, chat, devices, enrol, goals, inbox, push
from hivemind.entrance.routes.entrance import (
    DOOR_ROUTES,
    ENROLMENT_ROUTES,
    HELD_ROUTES,
    OPERATOR_ROUTES,
)
from hivemind.entrance.routes.hive import (
    CELL_ROUTES,
    EPISODE_ROUTES,
    FORAGE_ROUTES,
    HONEY_ROUTES,
    LLM_ROUTES,
    TASK_ROUTES,
    TRAIL_ROUTES,
    WARDEN_ROUTES,
)
from hivemind.entrance.routes.isolation import ROUTES as ISOLATION_ROUTES
from hivemind.entrance.routes.isolation import STING_CUT_ROUTES, SUPERSEDE_ROUTES
from hivemind.entrance.routes.later import SWARM_ROUTES, TOOL_ROUTES
from hivemind.entrance.routes.screens import (
    ATTENDANT_ROUTES,
    CAPPING_ROUTES,
    CELL_PAGE_ROUTES,
    COST_ROUTES,
    FLEET_ROUTES,
    METRICS_ROUTES,
    SUMMARY_ROUTES,
    THOUGHTS_ROUTES,
)
from hivemind.entrance.voice import VOICE_ROUTES

__all__ = ["RESOURCE_ROUTES"]

# One entry per resource: the rows it serves, as its module declares them.
RESOURCE_ROUTES: tuple[tuple[RouteSpec, ...], ...] = (
    auth.ROUTES,
    enrol.ROUTES,
    DOOR_ROUTES,
    ENROLMENT_ROUTES,
    HELD_ROUTES,
    OPERATOR_ROUTES,
    devices.ROUTES,
    goals.ROUTES,
    chat.ROUTES,
    VOICE_ROUTES,  # POST /v1/chat/audio, beside the chat it speaks into (hivemind.entrance.voice).
    IMAGE_ROUTES,  # POST /v1/nectar/images, roadmap step 12.12 (hivemind.entrance.images).
    inbox.ROUTES,
    push.ROUTES,
    TASK_ROUTES,
    CELL_ROUTES,
    WARDEN_ROUTES,
    FORAGE_ROUTES,
    EPISODE_ROUTES,
    TRAIL_ROUTES,
    LLM_ROUTES,
    HONEY_ROUTES,  # Roadmap step 12.9: the Honey browser's tree, items and search.
    TOOL_ROUTES,
    SWARM_ROUTES,
    ISOLATION_ROUTES,  # Roadmap step 10.6a: the human's isolate and lift levers.
    STING_CUT_ROUTES,  # Roadmap step 13.4a: the human's Sting Cut, break-glass.
    SUPERSEDE_ROUTES,  # Roadmap step 13.2a: moving the Hive Stand, loopback and break-glass.
    # Roadmap step 12.1: the Observation Hive's aggregated screens and the metrics endpoint.
    SUMMARY_ROUTES,
    FLEET_ROUTES,
    CELL_PAGE_ROUTES,
    ATTENDANT_ROUTES,
    THOUGHTS_ROUTES,
    CAPPING_ROUTES,
    COST_ROUTES,
    METRICS_ROUTES,
)
