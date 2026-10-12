"""Serve the capping resource: the Hive-wide queue of proposals and what came of them.

``GET /v1/capping`` (roadmap 12.11) answers proposals by tier and state, the gate's verdicts with
the failing check as a code, rollbacks, and sampled-audit findings and rates per tier, each item
with the bee and task it belongs to so the UI can link to the bee's thoughts. It is built from the
trail's ``capping`` family and needs ``observe``: no reviewer prose or action text is on the trail.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens``.

Key invariants:
    - Read only; codes, ids and times.

See Also:
    - hivemind.observation.api.capping for the view.
"""

from __future__ import annotations

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_capping
from hivemind.observation import CappingView

OBSERVE = "observe"  # The Capping view carries no words.

__all__ = ["ROUTES"]


async def get_capping(services: Services) -> CappingView:
    """Answer the Capping view.

    Args:
        services: The Entrance's services (the trail).

    Returns:
        Proposals by tier and state, verdicts, rollbacks, audits and audit rates.
    """
    # Latency: one indexed trail read of at most MAX_QUERY_LIMIT events.
    return await read_capping(services.hive)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/capping",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_capping,
        summary="Read the Capping queue: proposals, verdicts, rollbacks and sampled audits.",
        response_model=CappingView,
    ),
)
