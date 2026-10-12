"""Serve one Cell's page: its bees, work, Forage, Cell Wax, Alarms and Capping at a glance.

``GET /v1/cells/{cell_id}/page`` (roadmap 12.4) joins everything one Cell's page draws. It needs
``observe`` alone: the page carries ids, states and figures, and the bees' episode records, a
task's brief and a note's text stay behind ``observe:thoughts`` and C2 on their own routes, which
the ids the page returns open (``/v1/thoughts?principal=``, ``/v1/tasks/{id}/brief``). The existing
``GET /v1/cells/{cell_id}`` is unchanged.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens``.

Key invariants:
    - Read only; no personal content.

See Also:
    - hivemind.observation.api.cell_page for the page.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_cell_page
from hivemind.observation import CellPageView
from waggle.messages.base import CellIdField

OBSERVE = "observe"  # The page is read-only and carries no words.

CellIdPath = Annotated[CellIdField, Path(description="The Cell's id.")]

__all__ = ["ROUTES"]


async def get_cell_page(cell_id: CellIdPath, services: Services) -> CellPageView:
    """Answer one Cell's page.

    Args:
        cell_id: The Cell.
        services: The Entrance's services.

    Returns:
        The page.

    Raises:
        CellNotFoundError: No attached Warden supervises it and no lifecycle tracks it (404).
    """
    # Latency: the census's reads plus a handful of indexed store reads for the one Cell.
    return await read_cell_page(services.hive, cell_id, services.clock.now())


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/cells/{cell_id}/page",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_cell_page,
        summary="Read one Cell's page: bees, tasks, Forage, Cell Wax, Alarms and Capping.",
        response_model=CellPageView,
    ),
)
