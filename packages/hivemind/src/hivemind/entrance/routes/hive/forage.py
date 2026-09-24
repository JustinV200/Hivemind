"""Serve the forage resource: the Queen's Forage ledger as it stands.

Forage is the Hive's capacity, divided by the Queen by grant (codingrules 8.10). ``GET
/v1/forage`` reads her ledger directly (ADR-0032): every Cell's latest capacity report, every
live grant (its holder, budgets and spend, and its expiry), the headroom those leave in the shared
pool, and the Royal Reserve held back first. It needs ``observe``; changes to the ledger stream on
``/v1/forage/stream``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into the Queen's Forage ledger (reads only) and the
    view models.

Key invariants:
    - Read only; no grant reason and no model id in the answer.

See Also:
    - hivemind.observation.views.forage for the view.
"""

from __future__ import annotations

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.observation import ForageView, forage_view

OBSERVE = "observe"  # The Forage view is read-only.

__all__ = ["ROUTES"]


async def read_forage(services: Services) -> ForageView:
    """Read the Queen's Forage ledger.

    Args:
        services: The Entrance's services (the ledger).

    Returns:
        Capacities, live grants, headroom and the Royal Reserve.
    """
    # Latency: in-memory reads of the ledger's own tables; no store round trip.
    return forage_view(services.hive.ledger)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/forage",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=read_forage,
        summary="Read the Forage ledger: capacity, live grants, headroom, the Royal Reserve.",
        response_model=ForageView,
    ),
)
