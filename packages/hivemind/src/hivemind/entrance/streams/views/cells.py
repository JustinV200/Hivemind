"""Stream each Cell's status as it changes: ``/v1/cells/stream``.

The Fleet list and the Cell pages are redrawn from Cell status deltas (roadmap 12.4, 12.6). What
changes a Cell's view is always on the trail: its own lifecycle and lease edges (``cell.*``), its
Warden's mode edges (``warden.*``) and the tasks placed on it and leaving it (``task.*``). So the
view follows those families through the stream hub (ADR-0032), rebuilds the Cells' views from
the stores (the census, as ``GET /v1/cells`` does) whenever a batch arrives, and sends a
``CellFrame`` for each Cell whose view differs from the one it last sent. It subscribes first and
then sends every Cell once, so a client needs no separate read to start and misses nothing in
between. It needs ``observe``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Its
    row joins ``VIEWS``. Calls into the pump, the hub, the census and the frame models.

Key invariants:
    - A Cell is sent when its view changed since the last frame for it, and every Cell at first.

See Also:
    - hivemind.entrance.reads.census for how a Cell's view is built.
"""

from __future__ import annotations

from starlette.websockets import WebSocket

from hivemind.entrance.gate.params import Here, Services
from hivemind.entrance.gate.reads import HiveReads
from hivemind.entrance.gate.spec import BOTH_LISTENERS, SocketSpec, session_with
from hivemind.entrance.models.views import CellFrame, CellView
from hivemind.entrance.reads import cell_views
from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.socket import StreamContext, send_frame, serve_socket
from hivemind.entrance.streams.views.pump import pump, trail_subscription
from hivemind.pheromone import PheromoneEvent

_ACCESS = session_with("observe")  # A Cell's status carries nothing personal.
_FAMILIES = frozenset({"cell", "warden", "task"})  # What can change a Cell's view.

__all__ = ["CELLS_VIEW", "cell_stream"]


async def cell_stream(websocket: WebSocket, services: Services, here: Here) -> None:
    """Stream every Cell once, then each Cell whose status changed.

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
    """
    fleet = _Fleet(services.hive)

    async def view(context: StreamContext) -> CloseReason:
        """Subscribe, send every Cell, then send each change."""
        # Subscribed before the first read, so a change between the two is never missed.
        subscription = trail_subscription(context, "cells", _moves_a_cell)
        for frame in await fleet.changed():
            if not await send_frame(context.websocket, frame):
                subscription.close()
                return CloseReason.UNSUBSCRIBED
        return await pump(context, subscription, fleet.on_batch)

    await serve_socket(websocket, _ACCESS, view, services, here)


class _Fleet:
    """The Cells as this view last sent them, to send only what changed."""

    def __init__(self, reads: HiveReads) -> None:
        """Start having sent nothing."""
        self._reads = reads
        self._sent: dict[str, CellView] = {}

    async def on_batch(self, batch: tuple[PheromoneEvent, ...]) -> list[CellFrame]:
        """Rebuild after a batch of trail events; the frames of the Cells that changed."""
        return await self.changed()

    async def changed(self) -> list[CellFrame]:
        """Rebuild every Cell's view; a frame for each one that differs from the last sent."""
        # Latency: the census's local reads, a few per Cell.
        frames: list[CellFrame] = []
        for cell in await cell_views(self._reads):
            if self._sent.get(cell.id) != cell:
                self._sent[cell.id] = cell
                frames.append(CellFrame(cell=cell))
        return frames


def _moves_a_cell(event: PheromoneEvent) -> bool:
    """Accept the events that can change a Cell's view."""
    return event.family in _FAMILIES


CELLS_VIEW = SocketSpec(
    path="/v1/cells/stream",
    listeners=BOTH_LISTENERS,
    access=_ACCESS,
    endpoint=cell_stream,
    summary="Every Cell once, then each Cell whose status (tier, mode, lease, tasks) changed.",
    frame_model=CellFrame,
)
