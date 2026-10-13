"""Read the Capping view from the trail's ``capping`` family.

The Capping gate keeps its proposals in the Queen's memory and records every hop on the trail
before it counts as complete (codingrules 12), so the trail is where the whole queue can be read.
This module reads the newest ``capping`` events and folds them with the pure ``capping_view``. A
Cell page narrows the same read to one Cell.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.capping`` and the Cell page read. Calls into the trail
    and the pure builder.

Key invariants:
    - Only reads; the window is the newest ``MAX_QUERY_LIMIT`` capping events.

See Also:
    - hivemind.observation.api.capping for the fold.
"""

from __future__ import annotations

from hivemind.entrance.gate.reads import HiveReads
from hivemind.observation import CappingView, capping_view
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery

CAPPING_FAMILY = "capping"  # The trail family every Capping hop is recorded under.

__all__ = ["CAPPING_FAMILY", "read_capping"]


async def read_capping(reads: HiveReads) -> CappingView:
    """Read the Capping queue and what came of it.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        The Hive-wide Capping view; empty on a Hive that has proposed nothing.
    """
    # Latency: one local indexed trail read, at most MAX_QUERY_LIMIT events.
    events = await reads.trail.query(
        TrailQuery(family=CAPPING_FAMILY, newest_first=True, limit=MAX_QUERY_LIMIT)
    )
    return capping_view(events)
