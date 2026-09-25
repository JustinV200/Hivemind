"""Build the one row a resource the contract names, before its phase lands, answers with.

ADR-0040: a Landing Board resource whose subsystem is not built yet answers ``501 Not
Implemented`` naming the roadmap phase that fills it, so the contract names every resource from
the start: a client written against the document sees ``/v1/tools``, ``/v1/honey`` and
``/v1/swarm`` now, and learns from the answer when they arrive. ``not_built_row`` builds that row
for one resource: a GET on both listeners, behind ``observe`` like the reads it stands in for, and
answering ``NotBuiltView``. Each resource keeps its own module (one module per resource); this is
the shape they share.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.later``. Called
    by the tools, honey and swarm modules beside it. Calls into the gate's row types and the
    view models.

Key invariants:
    - A not-built row is a READ answering 501; it never touches a store.

See Also:
    - hivemind.observation.views.unbuilt for the answer.
"""

from __future__ import annotations

from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.observation import NotBuiltView

OBSERVE = "observe"  # What the read a later phase fills will need; the placeholder needs it too.
NOT_IMPLEMENTED = 501  # The status every not-built resource answers with.

__all__ = ["NOT_IMPLEMENTED", "not_built_row"]


def not_built_row(resource: str, phase: int, what: str) -> RouteSpec:
    """Build the row a resource answers 501 with until roadmap ``phase`` fills it.

    Args:
        resource: Its name, the path's first segment under ``/v1/`` (``tools``).
        phase: The roadmap phase that fills it.
        what: What the resource will hold, for the summary (``the Comb Registry's tools``).

    Returns:
        The row: ``GET /v1/<resource>``, both listeners, ``observe``, answering 501.
    """
    detail = f"The {resource} resource ({what}) arrives with roadmap phase {phase}."

    async def not_built() -> NotBuiltView:
        """Answer that this resource's phase has not landed yet."""
        return NotBuiltView(detail=detail, resource=resource, phase=phase)

    # FastAPI documents an endpoint by its name; each resource's gets its own.
    not_built.__name__ = f"read_{resource}"
    return RouteSpec(
        method="GET",
        path=f"/v1/{resource}",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=not_built,
        summary=f"{what[0].upper()}{what[1:]}: not built yet, answers 501 (phase {phase}).",
        status_code=NOT_IMPLEMENTED,
        response_model=NotBuiltView,
    )
