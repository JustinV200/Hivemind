"""Provide StreamSubscription: one subscriber's bounded queue, closed with a reason when it lags.

Every live view of the Hive Entrance reads from a feed that must never wait on it (ADR-0032): the
stream hub following the Pheromone Trail, and the telemetry board the Queen's Heartbeats reach.
Each subscriber gets a ``StreamSubscription``: the feed ``offer``s it every item it accepts, and
the view takes them in batches with ``next_batch``. The queue is bounded: a subscriber that falls
``backlog`` items behind is closed with ``FELL_BEHIND`` (losing what it had not read, so it
reconnects and catches up from a cursor) instead of slowing the feed, so one stuck browser never
delays anyone else's view or the Guard's reduce order. One class serves every feed, generic in
what it carries.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Built by the
    stream hub and the telemetry board; read by every view. Calls into nothing outside this
    package.

Key invariants:
    - A subscription never holds more than its backlog; overflowing closes it with FELL_BEHIND.
    - Closing is idempotent: the first reason wins, and ``on_close`` runs once.

See Also:
    - hivemind.entrance.streams.hub and .telemetry for the two feeds.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable

from hivemind.common.logging import get_logger
from hivemind.entrance.streams.errors import CloseReason, StreamClosedError

DEFAULT_BACKLOG = 512  # Items a subscriber may fall behind before it is closed as too slow.

log = get_logger(__name__)

__all__ = ["DEFAULT_BACKLOG", "StreamSubscription"]


class StreamSubscription[ItemT]:
    """One subscriber's bounded queue of items, closed with a reason when it falls behind.

    ``ItemT`` is what its feed carries: a trail event (the hub), a telemetry sample (the board).
    """

    def __init__(
        self,
        name: str,
        accepts: Callable[[ItemT], bool],
        backlog: int,
        on_close: Callable[[StreamSubscription[ItemT]], None],
    ) -> None:
        """Build a subscription; a feed does, through its own ``subscribe``.

        Args:
            name: What subscribed, for the log (a view's path, the reduce-order follower).
            accepts: Which items it wants.
            backlog: How many undelivered items it may hold; at least 1.
            on_close: Called once when it closes, so the feed drops it.
        """
        self._name = name
        self._accepts = accepts
        self._backlog = max(1, backlog)
        self._on_close = on_close
        self._items: deque[ItemT] = deque()
        # Set whenever an item arrives or the subscription closes; next_batch waits on it.
        self._ready = asyncio.Event()
        self._closed: CloseReason | None = None

    @property
    def closed(self) -> CloseReason | None:
        """Why it closed, or None while it is open."""
        return self._closed

    def offer(self, item: ItemT) -> None:
        """Queue ``item`` if the subscriber wants it; close the subscription when it is full.

        Args:
            item: One item, in the feed's order.
        """
        if self._closed is not None or not self._accepts(item):
            return
        # Past its backlog the subscriber is too slow to keep: it is told so and let go.
        if len(self._items) >= self._backlog:
            log.warning("entrance.stream_fell_behind", subscriber=self._name)
            self.close(CloseReason.FELL_BEHIND)
            return
        self._items.append(item)
        self._ready.set()

    async def next_batch(self) -> tuple[ItemT, ...]:
        """Wait for at least one item and return every item queued so far, oldest first.

        Returns:
            One or more items.

        Raises:
            StreamClosedError: The subscription closed (with its reason) and holds nothing more.
        """
        while not self._items:
            if self._closed is not None:
                raise StreamClosedError(self._closed)
            self._ready.clear()
            await self._ready.wait()
        batch = tuple(self._items)
        self._items.clear()
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
            self._items.clear()
        self._ready.set()
        self._on_close(self)
