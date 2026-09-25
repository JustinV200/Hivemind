"""Serve the trail resource: a filtered, paged read of the Pheromone Trail.

The Pheromone Trail is the Hive's audit log (codingrules 12), and it carries no content by
construction: ids, kinds, times and bounded identifier-only payloads. ``GET /v1/trail`` reads it
directly (ADR-0040) under ``observe``: filtered by family, kind and subject, bounded by ``since``
and ``until``, oldest first or newest first, a page at a time from a cursor. Each page's ``next``
names where the following page starts (an instant, and how many events at exactly that instant
the caller already has), to be passed back with the same filters. The query string is one
validated model (``TrailFilters``), so an unknown parameter is refused rather than ignored. New
events stream on ``/v1/trail/stream``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into ``hivemind.entrance.reads`` (``read_trail``).

Key invariants:
    - Read only; an event is answered as the trail holds it, nothing added.

See Also:
    - hivemind.entrance.reads.trail for the cursor.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads import TrailRead, read_trail
from hivemind.observation import TrailFilters, TrailPage

OBSERVE = "observe"  # The trail view is read-only.

__all__ = ["ROUTES"]


async def read_trail_page(
    services: Services, filters: Annotated[TrailFilters, Query()]
) -> TrailPage:
    """Read one page of the trail after the cursor.

    Args:
        services: The Entrance's services (the trail).
        filters: The filters, the cursor and the page size.

    Returns:
        The page, and where the next one starts.
    """
    # Latency: one local indexed trail read, at most MAX_QUERY_LIMIT rows.
    return await read_trail(services.hive.trail, TrailRead.of(filters))


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/trail",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=read_trail_page,
        summary="Read a filtered page of the Pheromone Trail from a cursor (no content).",
        response_model=TrailPage,
    ),
)
