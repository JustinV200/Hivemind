"""Read a page of a bee's thoughts: episode records after a cursor, and the Queen's autopilot rows.

Memory lists episode records newest first up to a limit, with no offset, so a page after a cursor
is cut from the newest ``THOUGHTS_WINDOW`` records: the cursor names a (time, id) pair and the pure
``thoughts_page`` keeps what is strictly older. History deeper than the window stays in memory and
is reachable through ``/v1/episodes`` per bee. For the Queen the read also pulls her recent
``queen.decided`` and ``alarm.handled`` trail events, the deterministic decisions.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.thoughts`` behind ``observe:thoughts`` and C2. Calls
    into memory (reads only), the trail and the pure builder.

Key invariants:
    - Only reads; the caller has already been checked for C2 (episode records may quote the human).

See Also:
    - hivemind.observation.api.thoughts for the page.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.entrance.gate.reads import HiveReads
from hivemind.observation import MAX_DECISIONS, ThoughtsPage, ThoughtsQuery, thoughts_page
from hivemind.pheromone import PheromoneEvent, TrailQuery

THOUGHTS_WINDOW = 2_000  # Newest records a page is cut from: deeper history is /v1/episodes'.
_DECISION_KINDS = ("queen.decided", "alarm.handled")  # The trail's deterministic decisions.

__all__ = ["THOUGHTS_WINDOW", "read_thoughts"]


async def read_thoughts(reads: HiveReads, query: ThoughtsQuery) -> ThoughtsPage:
    """Read one page of thoughts.

    Args:
        reads: The stores and live tables the Entrance reads.
        query: The validated query: whose, the cursor and the page size.

    Returns:
        The page.
    """
    # The route already required honey:clearance:c2, so every clearance is within allowance.
    # Latency: one local indexed read of the memory tables.
    records = await reads.memory.list_episodes(query.principal, HoneyClearance.C2, THOUGHTS_WINDOW)
    events: list[PheromoneEvent] = []
    for kind in _DECISION_KINDS:
        # Latency: one local indexed trail read per kind.
        events.extend(
            await reads.trail.query(TrailQuery(kind=kind, newest_first=True, limit=MAX_DECISIONS))
        )
    return thoughts_page(records, events, query)
