"""Stream the Forage ledger's deltas as they happen: ``/v1/forage/stream``.

The Observation Hive's Forage view redraws from deltas (codingrules 8.11). Every change to the
Queen's ledger that matters is a ``forage.*`` trail event recorded with it (a grant issued, grown
or shrunk, revoked or expired, a capacity report, a hosting plan, ceilings), so the view follows
those through the stream hub (ADR-0040) and sends, per event, a ``ForageFrame``: the event, the
grant it names as the ledger now holds it (null once the grant is gone, or when it names none),
and the shared pool's headroom after it. It needs ``observe``; the whole ledger is ``GET
/v1/forage``, which a client reads once and then follows here.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Its
    row joins ``VIEWS``. Calls into the pump, the hub, the Queen's Forage ledger (reads only) and
    the frame models.

Key invariants:
    - A frame's grant and headroom are read from the ledger when the frame is built: the newest.

See Also:
    - hivemind.entrance.routes.hive.forage for the whole ledger.
"""

from __future__ import annotations

from starlette.websockets import WebSocket

from hivemind.entrance.gate.params import Here, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, SocketSpec, session_with
from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.socket import StreamContext, serve_socket
from hivemind.entrance.streams.views.pump import pump, trail_subscription
from hivemind.observation import (
    ForageFrame,
    grant_view,
    headroom_view,
    trail_event_view,
)
from hivemind.pheromone import PheromoneEvent
from hivemind.queen import ForageLedger
from waggle.ids import GrantId

_ACCESS = session_with("observe")  # Ledger figures carry nothing personal.
_FAMILY = "forage"  # The trail family every ledger change is recorded under.

__all__ = ["FORAGE_VIEW", "forage_stream"]


async def forage_stream(websocket: WebSocket, services: Services, here: Here) -> None:
    """Stream a delta per ``forage.*`` event: the event, its grant now, the headroom after it.

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
    """
    ledger = services.hive.ledger

    async def frames(batch: tuple[PheromoneEvent, ...]) -> list[ForageFrame]:
        """One frame per ledger event, read against the ledger as it stands now."""
        return [_frame(ledger, event) for event in batch]

    async def view(context: StreamContext) -> CloseReason:
        """Send each ledger change as it is recorded."""
        return await pump(context, trail_subscription(context, "forage", _is_forage), frames)

    await serve_socket(websocket, _ACCESS, view, services, here)


def _frame(ledger: ForageLedger, event: PheromoneEvent) -> ForageFrame:
    """Build one delta: the event, the live grant it names (if any), the headroom now."""
    # A grant event's subject is the grant; any other names none, and a gone grant reads None.
    grant = ledger.grant(GrantId(event.subject_id))
    return ForageFrame(
        event=trail_event_view(event),
        grant=grant_view(grant) if grant is not None else None,
        headroom=headroom_view(ledger.headroom()),
    )


def _is_forage(event: PheromoneEvent) -> bool:
    """Accept every Forage ledger event."""
    return event.family == _FAMILY


FORAGE_VIEW = SocketSpec(
    path="/v1/forage/stream",
    listeners=BOTH_LISTENERS,
    access=_ACCESS,
    endpoint=forage_stream,
    summary="Forage ledger deltas: each forage.* event, its grant now, and the headroom after.",
    frame_model=ForageFrame,
)
