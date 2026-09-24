"""Stream new episode records as the bees think: ``/v1/episodes/stream``.

The thoughts view's "follow" mode streams new episodes as they happen (roadmap 12.3). An episode
record is written to memory together with its ``memory.episode`` trail event (codingrules 12: the
trail records that an episode happened, never its text), so the view follows those events through
the stream hub (ADR-0032) and reads each new record back from memory, the durable state, rather
than from anything a bee kept in hand. A batch of events is matched against memory's newest records
(the record's id is the event's subject); a record already purged or tainted by then is not sent,
since memory never hands one out. A client may follow one bee (``principal``). Records may quote
the human, so the view needs ``observe:thoughts`` and ``honey:clearance:c2``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Its
    row joins ``VIEWS``. Calls into the pump, the hub, memory (reads only) and the frame models.

Key invariants:
    - Answered only behind ``observe:thoughts`` and C2; a frame is a record memory still holds.

See Also:
    - hivemind.entrance.routes.hive.episodes for the read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from starlette.websockets import WebSocket

from hivemind.cell import HoneyClearance
from hivemind.entrance.gate.params import Here, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, SocketSpec, session_with
from hivemind.entrance.models.views import EpisodeFrame, episode_view
from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.socket import StreamContext, serve_socket
from hivemind.entrance.streams.views.pump import pump, trail_subscription
from hivemind.memory import EpisodeRecord, MemoryStore
from hivemind.memory.episodes import MAX_PRINCIPAL_CHARS
from hivemind.pheromone import PheromoneEvent

EPISODE_KIND = "memory.episode"  # Recorded with every episode record, naming it as its subject.
LOOKBACK_MARGIN = 32  # Newest records read beyond a batch's size: others may land meanwhile.
_ACCESS = session_with("observe:thoughts", c2=True)  # A bee's thinking: thoughts, and C2.

__all__ = ["EPISODES_VIEW", "EPISODE_KIND", "episode_stream"]


async def episode_stream(
    websocket: WebSocket,
    services: Services,
    here: Here,
    principal: Annotated[
        str | None, Query(max_length=MAX_PRINCIPAL_CHARS, description="Only this bee's.")
    ] = None,
) -> None:
    """Stream every new episode record, or one bee's (C2).

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
        principal: Only this bee's records; None for every bee's.
    """
    memory = services.hive.memory

    async def frames(batch: tuple[PheromoneEvent, ...]) -> list[EpisodeFrame]:
        """The batch's records, read back from memory, in the order they were recorded."""
        return await _records(memory, [event.subject_id for event in batch], principal)

    async def view(context: StreamContext) -> CloseReason:
        """Send each new record as it is written."""
        return await pump(context, trail_subscription(context, "episodes", _is_episode), frames)

    await serve_socket(websocket, _ACCESS, view, services, here)


async def _records(
    memory: MemoryStore, episode_ids: list[str], principal: str | None
) -> list[EpisodeFrame]:
    """Read the named records from memory's newest ones; skip one memory no longer holds."""
    limit = len(episode_ids) + LOOKBACK_MARGIN
    # The reader already holds honey:clearance:c2, so every record's clearance is within it.
    # Latency: one local indexed read of the memory tables, newest first.
    newest: dict[str, EpisodeRecord] = {
        record.id: record
        for record in await memory.list_episodes(principal, HoneyClearance.C2, limit)
    }
    return [
        EpisodeFrame(episode=episode_view(newest[episode_id]))
        for episode_id in episode_ids
        if episode_id in newest
    ]


def _is_episode(event: PheromoneEvent) -> bool:
    """Accept every episode record's event."""
    return event.kind == EPISODE_KIND


EPISODES_VIEW = SocketSpec(
    path="/v1/episodes/stream",
    listeners=BOTH_LISTENERS,
    access=_ACCESS,
    endpoint=episode_stream,
    summary="Every new episode record (optionally one bee's), as it is written "
    "(observe:thoughts, C2).",
    frame_model=EpisodeFrame,
)
