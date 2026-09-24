"""Serve the wardens resource: every attached Warden's state, pulse, Cell and sub-bees.

A Warden (the always-on supervisor of one Cell) is known only to the Queen, through its link and
its Heartbeats (codingrules 8.8: she is the only global view). ``GET /v1/wardens`` reads her live
tables directly, never through a method that acts (ADR-0032): each Warden's newest reported state
(OFFLINE once she marked it so), when she last heard from it and how many Heartbeats it missed,
its Cell, how many sub-bees its last Heartbeat listed and the live grants it holds. It needs
``observe``: the telemetry text a Heartbeat carries is on the telemetry stream, behind
``observe:thoughts`` and C2.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into ``hivemind.entrance.reads`` (the census).

Key invariants:
    - Read only; no telemetry text in the answer.

See Also:
    - hivemind.observation.views.wardens for the view.
"""

from __future__ import annotations

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads import warden_views
from hivemind.observation import WardenList

OBSERVE = "observe"  # The supervision tree is a read-only view.

__all__ = ["ROUTES"]


async def list_wardens(services: Services) -> WardenList:
    """List every Warden attached to the Queen, in attachment order.

    Args:
        services: The Entrance's services (the Queen's live tables, the board, the ledger).

    Returns:
        Every Warden's view.
    """
    return WardenList(wardens=list(warden_views(services.hive)))


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/wardens",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=list_wardens,
        summary="List every attached Warden: its state, pulse, Cell, sub-bees and grants.",
        response_model=WardenList,
    ),
)
