"""Provide ReduceOrderFollower: carry out every ``guard.reduce_ordered`` a Guard Bee records.

A Guard Bee (a Worker watching the Hive's security signals) cannot import the Hive Entrance, and
does not need to (ADR-0035): when one of its rules decides the door must narrow, it records a
``guard.reduce_ordered`` trail event, and the Entrance, which follows the trail anyway, reduces
itself. Narrowing access is always safe to do without judgement, so the follower obeys every order
at once, and a subscription that fell behind (orders may have been missed) reduces too before it
subscribes again. At start, ``catch_up`` obeys an order recorded while the Entrance was not
running, unless the Entrance was reopened on loopback after it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Run by the
    Hive Entrance's task group. Calls into the stream hub, the trail (one query at start) and the
    Entrance Reducer.

Key invariants:
    - Every order the hub delivers reduces the Entrance; reducing a reduced Entrance changes
      nothing but re-asserts the narrowing.
    - The follower never reopens anything.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md for the order.
    - hivemind.entrance.reducer for what reducing does.
"""

from __future__ import annotations

from hivemind.common.logging import get_logger
from hivemind.entrance.reducer import REOPENED_KIND, EntranceReducer, ReduceReason
from hivemind.entrance.streams.errors import CloseReason, StreamClosedError
from hivemind.entrance.streams.hub import StreamHub
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery

REDUCE_ORDERED_KIND = "guard.reduce_ordered"  # What a Guard Bee records to narrow the door.
ORDER_ACTOR = "system"  # A reduction the Entrance carries out for a Guard order: nobody's device.
_ORDER_BACKLOG = 16  # Orders are rare; more than this unread means the follower is stuck.

log = get_logger(__name__)

__all__ = ["ORDER_ACTOR", "REDUCE_ORDERED_KIND", "ReduceOrderFollower"]


class ReduceOrderFollower:
    """Reduce the Entrance on every Guard Bee order the trail carries."""

    def __init__(self, hub: StreamHub, reducer: EntranceReducer, trail: PheromoneTrail) -> None:
        """Subscribe at once, so no order the hub delivers after construction is missed.

        Args:
            hub: The stream hub following the trail.
            reducer: The Entrance Reducer.
            trail: The trail, read once at start for an order recorded while nobody followed.
        """
        self._hub = hub
        self._reducer = reducer
        self._trail = trail
        self._subscription = hub.subscribe("reduce-orders", _is_order, _ORDER_BACKLOG)

    async def catch_up(self) -> bool:
        """Obey an order newer than the last reopening, recorded while no Entrance followed.

        Returns:
            True when it reduced the Entrance.
        """
        order = await self._newest(REDUCE_ORDERED_KIND)
        if order is None:
            return False
        reopened = await self._newest(REOPENED_KIND)
        # The operator reopened on loopback after this order: that decision stands.
        if reopened is not None and reopened.at >= order.at:
            return False
        return await self._obey()

    async def run(self) -> None:
        """Obey every order until the hub stops; reduce and resubscribe after falling behind."""
        while True:
            try:
                while True:
                    await self._subscription.next_batch()
                    await self._obey()
            except StreamClosedError as closed:
                if closed.reason is not CloseReason.FELL_BEHIND:
                    return
                # Orders may have been missed: narrowing is always safe, so narrow now.
                await self._obey()
                self._subscription = self._hub.subscribe("reduce-orders", _is_order, _ORDER_BACKLOG)

    async def _obey(self) -> bool:
        """Reduce the Entrance for a Guard order."""
        changed = await self._reducer.reduce(ReduceReason.GUARD_ORDER, ORDER_ACTOR)
        log.info("entrance.reduce_order_obeyed", changed=changed)
        return changed

    async def _newest(self, kind: str) -> PheromoneEvent | None:
        """Return the newest event of ``kind`` on the trail, if any."""
        # Latency: one local indexed trail read.
        found = await self._trail.query(TrailQuery(kind=kind, newest_first=True, limit=1))
        return found[0] if found else None


def _is_order(event: PheromoneEvent) -> bool:
    """Return whether ``event`` is a Guard Bee's reduce order."""
    return event.kind == REDUCE_ORDERED_KIND
