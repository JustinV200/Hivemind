"""Serve the cost resource: spend per goal, slot and provider, and what a local slot would save.

``GET /v1/cost`` (roadmap 12.10) sums the trail's normalised ``Usage`` on completed model calls
per goal, per slot and per provider, with the hosted spend each slot would save if it were local.
It needs ``observe``: tokens, dollars and ids.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens``.

Key invariants:
    - Read only; no model id or key is ever in the answer.

See Also:
    - hivemind.observation.api.cost for the view.
"""

from __future__ import annotations

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_cost
from hivemind.observation import CostView

OBSERVE = "observe"  # The Cost view carries figures only.

__all__ = ["ROUTES"]


async def get_cost(services: Services) -> CostView:
    """Answer the Cost view.

    Args:
        services: The Entrance's services (the trail, the ledger and the chamber).

    Returns:
        Spend per goal, slot and provider, and each slot's local saving.
    """
    # Latency: one indexed trail read of at most MAX_QUERY_LIMIT events and a read per live grant.
    return await read_cost(services.hive, services.clock.now())


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/cost",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_cost,
        summary="Read spend per goal, slot and provider, and what a local slot would save.",
        response_model=CostView,
    ),
)
