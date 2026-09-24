"""Serve the human's two Cell isolation levers: isolate a Cell, and lift its isolation.

ADR-0035 (roadmap step 10.6a) leaves two things to the human alone: isolating the Hive Stand's own
lease (the Queen isolates any other Cell herself, never that one) and lifting any isolation (the
Queen never lifts on her own). ``POST /v1/cells/{cell_id}/isolate`` and ``/lift`` are those levers.
Both need an interactive device inside its step-up window (``require_step_up`` with nothing to
hold: a program cannot step up, so it is refused, never queued) and ``entrance:steward``, the
access family ADR-0031 reserves to the human alone (the operator and the devices approved with it;
no bee ever holds it): isolating or lifting a Cell is stewardship of the Hive, not giving it work
(``entrance:submit``) or reading it (``observe``). Both are served on both listeners, like every
other lever an interactive device pulls (locking a lost phone). Each goes through the Queen's door
(``QueenDoor.isolate_cell`` / ``.lift_isolation``, the one isolation path and the one lift); the
Entrance never writes a Queen table itself. A lift leaves tainted memory tainted until a judge
clears it, and the tasks the isolation paused paused.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.isolation``.
    Registered in the route table. Calls into the step-up gate and the Queen's door only.

Key invariants:
    - No isolation or lift happens without an interactive, stepped-up session.
    - Every refusal the Queen raises keeps its category: 404 for an unknown Cell or report, 409
      for nothing to lift, 403 for the isolation point's refusal.

See Also:
    - hivemind.queen.isolation for what each lever does.
    - docs/guard/isolation.md for the operator's view.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.entrance.auth.step_up import ActionKind
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.routes.isolation.views import (
    CellIsolationView,
    CellLiftView,
    IsolateBody,
    isolation_view,
    lift_view,
)
from waggle.ids import CellId

STEWARD = "entrance:steward"  # The human's own family (ADR-0031): no bee ever holds it.

CellIdPath = Annotated[
    str, Path(pattern=r"^cell_[0-9A-HJKMNP-TV-Z]{26}$", description="The Cell's id.")
]

__all__ = ["ROUTES", "STEWARD"]


async def isolate(
    cell_id: CellIdPath, body: IsolateBody, caller: CallerParam, services: Services
) -> CellIsolationView:
    """Isolate a Cell on the human's order: interactive, after step-up.

    Args:
        cell_id: The Cell to isolate; the Hive Stand's own included (only the human may).
        body: Why, and the Guard report it answers, if any.
        caller: The admitted caller: an interactive device inside its step-up window.
        services: The Entrance's services (the Queen's door).

    Returns:
        What the isolation did, or that the Cell was isolated already.
    """
    await require_step_up(services, caller, ActionKind.ISOLATE_CELL)
    # One Queen call: her one isolation path, bounded by her own pause wait (seconds at most).
    outcome = await services.queen.isolate_cell(
        CellId(cell_id), caller.device.id, body.reason, body.report_id
    )
    return isolation_view(outcome)


async def lift(cell_id: CellIdPath, caller: CallerParam, services: Services) -> CellLiftView:
    """Lift a Cell's isolation and the Queen's holds on it: interactive, after step-up.

    Args:
        cell_id: The Cell to lift.
        caller: The admitted caller: an interactive device inside its step-up window.
        services: The Entrance's services (the Queen's door).

    Returns:
        What the lift did; tainted memory stays tainted until a judge clears it.
    """
    await require_step_up(services, caller, ActionKind.LIFT_ISOLATION)
    # One Queen call: a few local writes (the holds, the wax, the event) and one backend call.
    return lift_view(await services.queen.lift_isolation(CellId(cell_id), caller.device.id))


_STEWARD = session_with(STEWARD)
_CELL = "/v1/cells/{cell_id}"

ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path=f"{_CELL}/isolate",
        listeners=BOTH_LISTENERS,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=isolate,
        summary="Isolate a Cell (interactive, after step-up); the only way onto the Hive Stand.",
        response_model=CellIsolationView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_CELL}/lift",
        listeners=BOTH_LISTENERS,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=lift,
        summary="Lift a Cell's isolation (interactive, after step-up); taint stays until judged.",
        response_model=CellLiftView,
    ),
)
