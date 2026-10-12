"""Serve the summary resource: the strip of core counters every Observation Hive screen shows.

Codingrules 8.11 keeps total active bees, active versus inactive Cells, Real versus Virtual Cells
and LLM versus autopilot activity always visible. ``GET /v1/summary`` answers them as one value: the
strip's first paint and its resync after a dropped stream (the strip then follows the live streams,
never polling). It needs ``observe``; every figure is a count.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens`` (the read) and
    the observation face (the model).

Key invariants:
    - Read only; counts, no words.

See Also:
    - hivemind.observation.api.summary for each counter's definition.
"""

from __future__ import annotations

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_summary
from hivemind.observation import SummaryView

OBSERVE = "observe"  # The strip is read-only and carries counts only.

__all__ = ["ROUTES"]


async def get_summary(services: Services) -> SummaryView:
    """Answer the summary strip's counters.

    Args:
        services: The Entrance's services (the stores and the clock).

    Returns:
        Active bees, active and inactive Cells, Real and Virtual Cells, awake and autopilot bees.
    """
    # Latency: the census's local reads plus two indexed trail and memory reads.
    return await read_summary(services.hive, services.clock.now())


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/summary",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_summary,
        summary="Read the summary strip: active bees, active/inactive and Real/Virtual Cells, "
        "LLM against autopilot activity.",
        response_model=SummaryView,
    ),
)
