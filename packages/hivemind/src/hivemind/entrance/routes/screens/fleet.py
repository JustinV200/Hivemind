"""Serve the fleet resource: every Cell in one table, with its tier, Mask state and badges.

The Fleet list (roadmap 12.6) is one read of everything the Hive knows about its Cells side by
side: kind, source, status, ``CombShieldLevel`` and Pheromone Mask state, the two badges (where the
Warden runs, where its models come from), current task and role, open Alarms, the highest live Cell
Wax severity and lease age, with the ids the UI links through and the three-tier legend. The
Real / Virtual / All filter is the client's, over the ``kind`` field. It needs ``observe``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens``.

Key invariants:
    - Read only; ids, states and figures, never a task's words or a note's text.

See Also:
    - hivemind.observation.api.fleet for the row.
"""

from __future__ import annotations

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_fleet
from hivemind.observation import FleetView

OBSERVE = "observe"  # The Fleet is read-only.

__all__ = ["ROUTES"]


async def get_fleet(services: Services) -> FleetView:
    """Answer the Fleet list.

    Args:
        services: The Entrance's services.

    Returns:
        One row per Cell and the tier legend.
    """
    # Latency: the census's reads per Cell plus a handful of indexed store reads.
    return await read_fleet(services.hive, services.clock.now())


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/fleet",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_fleet,
        summary="Read the Fleet list: every Cell with tier, Mask state, badges, work and Alarms.",
        response_model=FleetView,
    ),
)
