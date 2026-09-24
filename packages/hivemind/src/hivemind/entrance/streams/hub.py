"""Provide StreamHub: follow the Pheromone Trail once and fan its events out to every subscriber.

Clients never poll (codingrules 8.11): each live view of the Hive Entrance is a WebSocket fed from
durable state. ``StreamHub`` is the one reader of the trail for all of them (ADR-0032): ``run``
follows it with ``hivemind.pheromone.follow`` from the moment it starts, and hands every event to
every ``StreamSubscription`` whose filter accepts it. Each subscription is a bounded queue: a
subscriber that falls ``backlog`` events behind is closed with a reason instead of slowing the
reader, so one stuck browser never delays the Guard's reduce order or anyone else's view. A view
is a registration: it subscribes with a filter and turns batches of events into frames.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Run by the
    Hive Entrance's task group; subscribed to by the stream views and the reduce-order follower.
    Calls into ``hivemind.pheromone`` (``follow``) only.

Key invariants:
    - The trail is read by one follower, whatever the number of subscribers.
    - A subscription never holds more than its backlog; overflowing closes it with FELL_BEHIND.
    - Closing is idempotent, and a closed subscription is dropped from the hub.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "One stream per view, fed
      from durable state".
    - hivemind.pheromone.trail.tail for ``follow``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from datetime import datetime

from hivemind.common.logging import get_logger
from hivemind.entrance.streams.errors import CloseReason, StreamClosedError
from hivemind.pheromone import DEFAULT_POLL_INTERVAL_S, PheromoneEvent, PheromoneTrail, follow
from waggle.clock import Clock

DEFAULT_BACKLOG = 512  # Events a subscriber may fall behind before it is closed as too slow.

EventFilter = Callable[[PheromoneEvent], bool]  # Which events a subscription wants.

log = get_logger(__name__)

__all__ = ["DEFAULT_BACKLOG", "EventFilter", "StreamHub", "StreamSubscription"]


class StreamSubscription:
    """One subscriber's bounded queue of trail events, closed with a reason when it falls behind."""

    def __init__(
        self,
        name: str,
        accepts: EventFilter,
        backlog: int,
        on_close: Callable[[StreamSubscription], None],
    ) -> None:
        """Build a subscription; the hub does, through ``StreamHub.subscribe``.

        Args:
            name: What subscribed, for the log (a view's path, the reduce-order follower).
            accepts: Which events it wants.
            backlog: How many undelivered events it may hold; >= 1.
            on_close: Called once when it closes, so the hub drops it.
        """
        self._name = name
        self._accepts = accepts
        self._backlog = max(1, backlog)
        self._on_close = on_close
        self._events: deque[PheromoneEvent] = deque()
        # Set whenever an event arrives or the subscription closes; next_batch waits on it.
        self._ready = asyncio.Event()
        self._closed: CloseReason | None = None

    @property
    def closed(self) -> CloseReason | None:
        """Why it closed, or None while it is open."""
        return self._closed

    def offer(self, event: PheromoneEvent) -> None:
        """Queue ``event`` if the subscriber wants it; close the subscription when it is full.

        Args:
            event: One trail event, in trail order.
        """
        if self._closed is not None or not self._accepts(event):
            return
        # Past its backlog the subscriber is too slow to keep: it is told so and let go.
        if len(self._events) >= self._backlog:
            log.warning("entrance.stream_fell_behind", subscriber=self._name)
            self.close(CloseReason.FELL_BEHIND)
            return
        self._events.append(event)
        self._ready.set()

    async def next_batch(self) -> tuple[PheromoneEvent, ...]:
        """Wait for at least one event and return every event queued so far, oldest first.

        Returns:
            One or more events.

        Raises:
            StreamClosedError: The subscription closed (with its reason) and holds nothing more.
        """
        while not self._events:
            if self._closed is not None:
                raise StreamClosedError(self._closed)
            self._ready.clear()
            await self._ready.wait()
        batch = tuple(self._events)
        self._events.clear()
        return batch

    def close(self, reason: CloseReason = CloseReason.UNSUBSCRIBED) -> None:
        """Close the subscription; a second call changes nothing.

        Args:
            reason: Why; a subscriber that fell behind loses what it had not read.
        """
        if self._closed is not None:
            return
        self._closed = reason
        if reason is CloseReason.FELL_BEHIND:
            self._events.clear()
        self._ready.set()
        self._on_close(self)


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
        self._subscriptions: set[StreamSubscription] = set()

    @property
    def subscribers(self) -> int:
        """How many subscriptions are open."""
        return len(self._subscriptions)

    def subscribe(
        self, name: str, accepts: EventFilter, backlog: int = DEFAULT_BACKLOG
    ) -> StreamSubscription:
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
