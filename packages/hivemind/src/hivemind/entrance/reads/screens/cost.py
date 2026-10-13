"""Read the Cost view from the trail's ``llm.call`` events.

Every completed model call is an ``llm.call`` event with its slot, provider and normalised usage
(codingrules 8.6); a call whose lane carried a goal id names it in its payload, and one whose lane
carried only a grant is attributed through the grant's task's goal. This module reads the newest
calls, resolves the live grants to goals, and hands both to the pure ``cost_view``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.cost`` and the metrics read. Calls into the trail, the
    ledger, the Brood Chamber and the pure builder.

Key invariants:
    - Only reads; the window is the newest ``MAX_QUERY_LIMIT`` calls.

See Also:
    - hivemind.observation.api.cost for the fold.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.brood_chamber import TaskNotFoundError
from hivemind.entrance.gate.reads import HiveReads
from hivemind.observation import CostView, cost_view
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery

__all__ = ["call_events", "grant_goals", "read_cost"]


async def read_cost(reads: HiveReads, now: datetime) -> CostView:
    """Read spend per goal, slot and provider.

    Args:
        reads: The stores and live tables the Entrance reads.
        now: The instant the view is built for.

    Returns:
        The Cost view; empty on a Hive that has made no call.
    """
    return cost_view(await call_events(reads), await grant_goals(reads), now)


async def call_events(reads: HiveReads) -> list[PheromoneEvent]:
    """Read the newest completed model calls.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        Up to ``MAX_QUERY_LIMIT`` ``llm.call`` events, newest first.
    """
    # Latency: one local indexed trail read, at most MAX_QUERY_LIMIT events.
    query = TrailQuery(family="llm", kind="llm.call", newest_first=True, limit=MAX_QUERY_LIMIT)
    return list(await reads.trail.query(query))


async def grant_goals(reads: HiveReads) -> dict[str, str]:
    """Map each live grant to the goal of the task it was issued for.

    Args:
        reads: The stores and live tables the Entrance reads.

    Returns:
        Grant id to goal id, for grants issued for a task the chamber still holds.
    """
    goals: dict[str, str] = {}
    for grant in reads.ledger.live_grants():
        if grant.task_id is None:
            continue
        try:
            # Latency: one local primary-key read of the Brood Chamber per live grant.
            goals[grant.id] = (await reads.chamber.get(grant.task_id)).goal_id
        except TaskNotFoundError:
            continue  # A grant outliving its task: not attributable.
    return goals
