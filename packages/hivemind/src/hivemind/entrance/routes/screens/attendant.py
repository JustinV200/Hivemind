"""Serve the attendant resource: the inbox behind the Queen or a Warden, and its task graph.

``GET /v1/attendant/{principal}`` (roadmap 12.7) takes ``queen`` or an attached Warden's id and
answers the ordered inbox with its scores, the task graph (nodes with placement and open Alarms,
and dependency edges), what finished lately and what is next. The inbox is rebuilt from durable
state and scored with the Attendant's own scorer (the live tick queue is not readable), and the
view says so. It needs ``observe``: ids, kinds and scores, never a task's words.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens``.

Key invariants:
    - Read only; an unknown principal is a 404 with its own code.

See Also:
    - hivemind.observation.api.attendant for the view.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_attendant
from hivemind.observation import AttendantView

OBSERVE = "observe"  # The view is read-only and carries no words.
_PRINCIPAL_PATTERN = r"^(queen|warden_[0-9A-HJKMNP-TV-Z]{26})$"  # The Queen or one Warden's id.

PrincipalPath = Annotated[
    str, Path(pattern=_PRINCIPAL_PATTERN, description="queen, or an attached Warden's id.")
]

__all__ = ["ROUTES"]


async def get_attendant(principal: PrincipalPath, services: Services) -> AttendantView:
    """Answer the Attendant view for the Queen or one Warden.

    Args:
        principal: ``queen`` or a Warden's id.
        services: The Entrance's services.

    Returns:
        The reconstructed inbox, the task graph, what finished and what is next.

    Raises:
        PrincipalNotFoundError: No attached Warden has that id (404).
    """
    # Latency: a few indexed chamber, trail and chat reads.
    return await read_attendant(services.hive, principal, services.clock.now())


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/attendant/{principal}",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_attendant,
        summary="Read the Attendant view for the Queen or a Warden: inbox, task graph, next.",
        response_model=AttendantView,
    ),
)
