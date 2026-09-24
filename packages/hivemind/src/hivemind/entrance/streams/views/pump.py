"""Pump a subscription into a socket: the loop every Hive view shares, and its subscriptions.

Each live view of the Hive (codingrules 8.11: views subscribe, they never poll) is the same loop
over a different feed: wait for a batch from its subscription, turn it into frames, send each
frame, and stop with the reason the subscription closed (FELL_BEHIND when the reader lagged past
the backlog, SHUTTING_DOWN when the Entrance stops) or when the client is gone. ``pump`` is that
loop; ``trail_subscription`` opens a subscription to the stream hub (the one trail follower) with
the Entrance's configured backlog, and ``telemetry_subscription`` one to the telemetry board. A
view subscribes before it sends any snapshot, so nothing that happens meanwhile is missed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Used
    by every view module beside it. Calls into the socket lifecycle's ``send_frame``, the hub and
    the board.

Key invariants:
    - ``pump`` always closes the subscription it was given, however it ends.
    - Every subscription is bounded by ``StreamServices.backlog``.

See Also:
    - hivemind.entrance.streams.subscription for the bounded queue.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel

from hivemind.entrance.streams.errors import CloseReason, StreamClosedError
from hivemind.entrance.streams.hub import EventFilter
from hivemind.entrance.streams.socket import StreamContext, send_frame
from hivemind.entrance.streams.subscription import StreamSubscription
from hivemind.entrance.streams.telemetry import TelemetrySample
from hivemind.pheromone import PheromoneEvent

__all__ = ["Frames", "pump", "telemetry_subscription", "trail_subscription"]

# Turns one batch from a feed into the frames to send, in order (reading stores as it needs).
type Frames[ItemT] = Callable[[tuple[ItemT, ...]], Awaitable[Sequence[BaseModel]]]


async def pump[ItemT](
    context: StreamContext, subscription: StreamSubscription[ItemT], frames: Frames[ItemT]
) -> CloseReason:
    """Send every batch's frames until the subscription closes or the client leaves.

    Args:
        context: The admitted socket and the Entrance's services.
        subscription: What the view subscribed to; closed here, however this ends.
        frames: Turns a batch into frames.

    Returns:
        Why the view ended: the subscription's close reason, or UNSUBSCRIBED when the client left.
    """
    try:
        while True:
            # External wait: the next batch from the feed, for as long as the socket lives.
            for frame in await frames(await subscription.next_batch()):
                if not await send_frame(context.websocket, frame):
                    return CloseReason.UNSUBSCRIBED
    except StreamClosedError as closed:
        return closed.reason
    finally:
        subscription.close()


def trail_subscription(
    context: StreamContext, name: str, accepts: EventFilter
) -> StreamSubscription[PheromoneEvent]:
    """Subscribe a view to the trail events it wants, bounded by the Entrance's backlog.

    Args:
        context: The admitted socket and the Entrance's services.
        name: The view, for the log.
        accepts: Which events it wants.

    Returns:
        The subscription.
    """
    streams = context.services.streams
    return streams.hub.subscribe(name, accepts, streams.backlog)


def telemetry_subscription(
    context: StreamContext, name: str
) -> StreamSubscription[TelemetrySample]:
    """Subscribe a view to every Heartbeat sample, bounded by the Entrance's backlog.

    Args:
        context: The admitted socket and the Entrance's services.
        name: The view, for the log.

    Returns:
        The subscription.
    """
    return context.services.hive.telemetry.subscribe(name, context.services.streams.backlog)
