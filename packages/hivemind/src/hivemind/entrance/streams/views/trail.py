"""Stream the Pheromone Trail as it is written: ``/v1/trail/stream``.

The Observation Hive's trail view follows the audit log live (codingrules 8.11). The stream hub
already follows the trail once for every view (ADR-0032); this view subscribes to the events a
client asked for (a family, a kind, or everything) and sends each as a ``TrailFrame``, carrying
exactly what ``GET /v1/trail`` answers for it. It needs ``observe``: trail events carry no content
by construction. A client that falls behind is closed with FELL_BEHIND and reconnects, reading
what it missed from ``GET /v1/trail`` with a cursor.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Its
    row joins ``VIEWS``. Calls into the pump, the hub and the trail's view models.

Key invariants:
    - A frame is the event as the trail holds it; nothing is added.

See Also:
    - hivemind.entrance.routes.hive.trail for the paged read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from starlette.websockets import WebSocket

from hivemind.entrance.gate.params import Here, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, SocketSpec, session_with
from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.socket import StreamContext, serve_socket
from hivemind.entrance.streams.views.pump import pump, trail_subscription
from hivemind.observation import MAX_FILTER_CHARS, TrailFrame, trail_event_view
from hivemind.pheromone import PheromoneEvent

_ACCESS = session_with("observe")  # Trail events carry no content: observing is enough.
_Filter = Annotated[str | None, Query(max_length=MAX_FILTER_CHARS)]

__all__ = ["TRAIL_VIEW", "trail_stream"]


async def trail_stream(
    websocket: WebSocket,
    services: Services,
    here: Here,
    family: _Filter = None,
    kind: _Filter = None,
) -> None:
    """Stream every new trail event, or only one family's or one kind's.

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
        family: Only this family (task, cell, forage, ...).
        kind: Only this kind (task.assigned, ...).
    """

    def wanted(event: PheromoneEvent) -> bool:
        """Accept the events the client asked for."""
        return (family is None or event.family == family) and (kind is None or event.kind == kind)

    async def view(context: StreamContext) -> CloseReason:
        """Send each wanted event as it is written."""
        return await pump(context, trail_subscription(context, "trail", wanted), _frames)

    await serve_socket(websocket, _ACCESS, view, services, here)


async def _frames(batch: tuple[PheromoneEvent, ...]) -> list[TrailFrame]:
    """One frame per event, in trail order."""
    return [TrailFrame(event=trail_event_view(event)) for event in batch]


TRAIL_VIEW = SocketSpec(
    path="/v1/trail/stream",
    listeners=BOTH_LISTENERS,
    access=_ACCESS,
    endpoint=trail_stream,
    summary="Every new Pheromone Trail event (optionally one family or kind), as it is written.",
    frame_model=TrailFrame,
)
