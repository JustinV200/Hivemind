"""Serve the cells resource: every Cell the Hive knows, and one Cell, with its tiers and work.

The Observation Hive's Fleet list and Cell pages read Cells here (codingrules 8.11): every Cell,
Real and Virtual, with its Comb Shield tier (always shown), access level and mode, Pheromone Mask
state (null until roadmap 6.13 tracks it), lease or Virtual status, Warden and the unfinished
tasks placed on it. The census joins these from the stores and the Queen's live tables, read
directly (ADR-0032). Both routes need ``observe``; nothing here is personal. Isolating a Cell and
lifting isolation are later steps (10.6a), not read routes, so they are not here.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into ``hivemind.entrance.reads`` (the census).

Key invariants:
    - Read only: no route here changes a Cell.

See Also:
    - hivemind.entrance.reads.census for how each field is found.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.models.views import CellList, CellView
from hivemind.entrance.reads import cell_view, cell_views
from waggle.messages.base import CellIdField

OBSERVE = "observe"  # The Fleet and the Cell pages are read-only views.

CellIdPath = Annotated[CellIdField, Path(description="The Cell's id.")]

__all__ = ["ROUTES"]


async def list_cells(services: Services) -> CellList:
    """List every Cell: attached Wardens' Cells first, then Virtual Cells without one.

    Args:
        services: The Entrance's services (the stores and live tables read).

    Returns:
        Every Cell's view.
    """
    # Latency: a few local reads per Cell (trail edges) and four for placed tasks.
    return CellList(cells=list(await cell_views(services.hive)))


async def read_cell(cell_id: CellIdPath, services: Services) -> CellView:
    """Read one Cell.

    Args:
        cell_id: The Cell.
        services: The Entrance's services.

    Returns:
        Its view.

    Raises:
        CellNotFoundError: No attached Warden supervises it and no lifecycle tracks it (404).
    """
    # Latency: the census's local reads; a Hive has few Cells, so it is built whole.
    return await cell_view(services.hive, cell_id)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/cells",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=list_cells,
        summary="List every Cell, Real and Virtual, with its tier, mode, lease and tasks.",
        response_model=CellList,
    ),
    RouteSpec(
        method="GET",
        path="/v1/cells/{cell_id}",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=read_cell,
        summary="Read one Cell.",
        response_model=CellView,
    ),
)
