"""Stream every bee's telemetry from the Queen's Heartbeats: ``/v1/telemetry/stream``.

"Full read access to any bee" means every episode record and every telemetry sample (codingrules
8.11). A Heartbeat never reaches the trail, so this view is fed by the telemetry board the Queen's
``on_heartbeat`` hook fills (ADR-0032: hooks the composition root wires where the trail is not the
source), not by the hub. Each Heartbeat becomes one frame per bee: the Warden's own report, then
one per sub-bee row, each with its context use, spend, state and its own goal line, last actions
and blockers. Those words are the bee's, which may quote the human, so the view needs
``observe:thoughts`` and ``honey:clearance:c2``. A client may follow one Warden's bees
(``warden_id``). A reader that lags is closed with FELL_BEHIND, like every view.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Its
    row joins ``VIEWS``. Calls into the pump, the telemetry board and the frame models.

Key invariants:
    - Answered only behind ``observe:thoughts`` and C2.

See Also:
    - hivemind.entrance.streams.telemetry for the board.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from starlette.websockets import WebSocket

from hivemind.entrance.gate.params import Here, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, SocketSpec, session_with
from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.socket import StreamContext, serve_socket
from hivemind.entrance.streams.telemetry import TelemetrySample
from hivemind.entrance.streams.views.pump import pump, telemetry_subscription
from hivemind.observation import TelemetryFrame, telemetry_samples
from waggle.messages.base import WardenIdField

_ACCESS = session_with("observe:thoughts", c2=True)  # A bee's own words: thoughts, and C2.

__all__ = ["TELEMETRY_VIEW", "telemetry_stream"]


async def telemetry_stream(
    websocket: WebSocket,
    services: Services,
    here: Here,
    warden_id: Annotated[
        WardenIdField | None, Query(description="Only this Warden's bees.")
    ] = None,
) -> None:
    """Stream a sample per bee from every Heartbeat the Queen receives (C2).

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
        warden_id: Only this Warden's bees; None for every Warden's.
    """

    async def frames(batch: tuple[TelemetrySample, ...]) -> list[TelemetryFrame]:
        """One frame per bee per Heartbeat, in the order they arrived."""
        return [
            TelemetryFrame(sample=sample)
            for beat in batch
            if warden_id is None or beat.warden_id == warden_id
            for sample in telemetry_samples(beat.warden_id, beat.heartbeat, beat.at)
        ]

    async def view(context: StreamContext) -> CloseReason:
        """Send each Heartbeat's samples as the Queen records it."""
        return await pump(context, telemetry_subscription(context, "telemetry"), frames)

    await serve_socket(websocket, _ACCESS, view, services, here)


TELEMETRY_VIEW = SocketSpec(
    path="/v1/telemetry/stream",
    listeners=BOTH_LISTENERS,
    access=_ACCESS,
    endpoint=telemetry_stream,
    summary="Every bee's telemetry from each Heartbeat, as it arrives (observe:thoughts, C2).",
    frame_model=TelemetryFrame,
)
