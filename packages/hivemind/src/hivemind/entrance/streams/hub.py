"""Provide StreamHub: follow the Pheromone Trail once and fan its events out to every subscriber.

Clients never poll (codingrules 8.11): each live view of the Hive Entrance is a WebSocket fed from
durable state. ``StreamHub`` is the one reader of the trail for all of them (ADR-0040): ``run``
follows it with ``hivemind.pheromone.follow`` from the moment it starts, and hands every event to
every ``StreamSubscription`` whose filter accepts it. Each subscription is a bounded queue: a
subscriber that falls ``backlog`` events behind is closed with a reason instead of slowing the
reader, so one stuck browser never delays the Guard's reduce order or anyone else's view. A view
is a registration: it subscribes with a filter and turns batches of events into frames. What the
trail does not carry (a Heartbeat's telemetry) comes from ``hivemind.entrance.streams.telemetry``
through the same kind of subscription.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Run by the
    Hive Entrance's task group; subscribed to by the stream views and the reduce-order follower.
    Calls into ``hivemind.pheromone`` (``follow``) and the subscription beside it only.

Key invariants:
    - The trail is read by one follower, whatever the number of subscribers.
    - A subscription never holds more than its backlog; overflowing closes it with FELL_BEHIND.
    - Closing is idempotent, and a closed subscription is dropped from the hub.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md, "One stream per view, fed
      from durable state".
    - hivemind.pheromone.trail.tail for ``follow``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.subscription import DEFAULT_BACKLOG, StreamSubscription
from hivemind.pheromone import DEFAULT_POLL_INTERVAL_S, PheromoneEvent, PheromoneTrail, follow
from waggle.clock import Clock

EventFilter = Callable[[PheromoneEvent], bool]  # Which events a subscription wants.

__all__ = ["DEFAULT_BACKLOG", "EventFilter", "StreamHub", "StreamSubscription"]


class StreamHub:
    """The one follower of the trail, fanning each event out to every subscription."""

    def __init__(
        self,
        trail: PheromoneTrail,
        clock: Clock,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        """Build a hub with no subscribers.

        Args:
            trail: The Hive's trail.
            clock: Paces the follower's polls.
            poll_interval_s: Seconds between polls once the trail is caught up; > 0.
        """
        self._trail = trail
        self._clock = clock
        self._poll_interval_s = poll_interval_s
        self._subscriptions: set[StreamSubscription[PheromoneEvent]] = set()

    @property
    def subscribers(self) -> int:
        """How many subscriptions are open."""
        return len(self._subscriptions)

    def subscribe(
        self, name: str, accepts: EventFilter, backlog: int = DEFAULT_BACKLOG
    ) -> StreamSubscription[PheromoneEvent]:
        """Open a subscription to every event ``accepts`` from now on.

        Args:
            name: What subscribes, for the log.
            accepts: Which events it wants.
            backlog: How far it may fall behind before it is closed.

        Returns:
            The subscription; close it when done.
        """
        subscription = StreamSubscription(name, accepts, backlog, self._subscriptions.discard)
        self._subscriptions.add(subscription)
        return subscription

    async def run(self, since: datetime | None = None) -> None:
        """Follow the trail and fan every event out, until cancelled.

        Args:
            since: Where to start; None starts now, so a subscriber sees what happens from here.
        """
        start = since if since is not None else self._clock.now()
        try:
            # Latency: one local trail read per poll; follow sleeps on the clock between polls.
            async for event in follow(self._trail, self._clock, self._poll_interval_s, start):
                for subscription in tuple(self._subscriptions):
                    subscription.offer(event)
        finally:
            # A hub that stops leaves no subscriber waiting on events that will never come.
            for subscription in tuple(self._subscriptions):
                subscription.close(CloseReason.SHUTTING_DOWN)
