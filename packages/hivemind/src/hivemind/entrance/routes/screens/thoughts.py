"""Serve the thoughts resource: a bee's episode records page by page, and the Queen's rules.

``GET /v1/thoughts`` (roadmap 12.3) is the paginated companion of ``GET /v1/episodes``, which stays
as it is (the contract is additive): the same records, newest first, one bee's with ``principal``,
continued with the ``cursor`` a page returns. For the Queen the page also lists her deterministic
decisions with the action and reason recorded. A record may quote the human, so the route needs
``observe:thoughts`` and ``honey:clearance:c2``; a tainted record is never answered (memory
refuses it).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens``.

Key invariants:
    - Answered only behind ``observe:thoughts`` and C2; read only.

See Also:
    - hivemind.entrance.routes.hive.episodes for the unpaginated read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_thoughts
from hivemind.observation import ThoughtsPage, ThoughtsQuery

THOUGHTS = "observe:thoughts"  # Reading a bee's thinking is its own capability (8.11).

__all__ = ["ROUTES"]


async def get_thoughts(
    services: Services, query: Annotated[ThoughtsQuery, Query()]
) -> ThoughtsPage:
    """Answer one page of thoughts (C2).

    Args:
        services: The Entrance's services (memory and the trail).
        query: Whose records, the cursor and the page size.

    Returns:
        The page: records newest first, the next cursor, and for the Queen her autopilot rows.
    """
    # Latency: one indexed memory read and two indexed trail reads.
    # The Queen's records carry her Hive's id, so 'queen' is read under it.
    hive_id = services.enrolment.records.identity.hive_id
    return await read_thoughts(services.hive, query, hive_id)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/thoughts",
        listeners=BOTH_LISTENERS,
        access=session_with(THOUGHTS, c2=True),
        effect=RouteEffect.READ,
        endpoint=get_thoughts,
        summary="Read a page of a bee's episode records, and the Queen's autopilot decisions "
        "(observe:thoughts, C2).",
        response_model=ThoughtsPage,
    ),
)
