"""Serve the episodes resource: the bees' thinking, one episode record at a time (C2).

Thoughts are memory, not audit (codingrules 12): every awake episode and every autopilot decision
leaves an episode record, and the Observation Hive's thoughts view reads them in full for any bee
(codingrules 8.11). ``GET /v1/episodes`` reads them from memory directly (ADR-0032), newest first,
optionally one bee's (``principal``). A record may quote the human, so the route needs
``observe:thoughts`` and ``honey:clearance:c2``; a record tainted by isolation or quarantine is
never answered (memory refuses it). New records stream on ``/v1/episodes/stream``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.hive``.
    Registered in the route table. Calls into memory (reads only) and the view models.

Key invariants:
    - Answered only behind ``observe:thoughts`` and C2; read only.

See Also:
    - hivemind.memory.episodes for the record.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from hivemind.cell import HoneyClearance
from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.memory.episodes import MAX_PRINCIPAL_CHARS
from hivemind.observation import EpisodeList, episode_view

THOUGHTS = "observe:thoughts"  # Reading a bee's thinking is its own capability (8.11).
DEFAULT_EPISODE_PAGE = 50  # Records answered when the caller does not say.
MAX_EPISODE_PAGE = 500  # Most records one read answers.

__all__ = ["ROUTES"]


async def list_episodes(
    services: Services,
    principal: Annotated[
        str | None,
        Query(max_length=MAX_PRINCIPAL_CHARS, description="Only this bee's (its id or role)."),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_EPISODE_PAGE, description="Most records to return.")
    ] = DEFAULT_EPISODE_PAGE,
) -> EpisodeList:
    """Read the newest episode records, optionally one bee's (C2).

    Args:
        services: The Entrance's services (memory).
        principal: Only this bee's records.
        limit: Most records to return.

    Returns:
        The records, newest first.
    """
    # The route already required honey:clearance:c2, so every clearance is within allowance.
    # Latency: one local indexed read of the memory tables.
    records = await services.hive.memory.list_episodes(principal, HoneyClearance.C2, limit)
    return EpisodeList(episodes=[episode_view(record) for record in records])


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/episodes",
        listeners=BOTH_LISTENERS,
        access=session_with(THOUGHTS, c2=True),
        effect=RouteEffect.READ,
        endpoint=list_episodes,
        summary="Read the newest episode records: the bees' thinking (observe:thoughts, C2).",
        response_model=EpisodeList,
    ),
)
