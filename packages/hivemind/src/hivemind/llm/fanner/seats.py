"""Define SeatMeter: the per-binding concurrency limit FannerLane acquires and releases per call.

Codingrules section 8.10: the Fanner is "the only place seat counts are enforced, so a grant is a
fact rather than a suggestion." A `SeatMeter` is that enforcement for one binding (a manifest
`[llm.slots]` key): a fixed number of concurrent calls, and a queue for everyone else, ordered by
tempo (how fast a task must be done and how right it must be) rather than plain arrival order --
"a CRITICAL/HIGH-tempo caller is admitted before a LOW one" (roadmap step 3.12a). `asyncio.Event`
plus a small priority heap does the ordering; there is no busy loop anywhere in this module.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.fanner`. Owned one per
    binding by `hivemind.llm.fanner.lane.Fanner`, acquired and released once per call by
    `hivemind.llm.fanner.lane.FannerLane.complete`. Calls into `hivemind.forage.tempo` (for
    `AccuracyBar` and `Tempo`) and `waggle.clock` only.

Key invariants:
    - A released seat is handed directly to the highest-priority waiter, never merely counted back
      as free and left for the next `acquire()` call to race for: `release()` holds the meter's
      lock for exactly as long as it takes to pop and wake that one waiter, so the priority order
      `acquire()` established is never defeated by a concurrent new arrival.
    - `_waiters` is a `(priority, arrival sequence, event)` min-heap; the sequence is unique per
      meter and strictly increasing, so two tuples never tie on their first two elements and
      `heapq` never has to compare the (unorderable) `asyncio.Event` objects themselves.

See Also:
    - .claude/codingrules.md section 8.10 for "the only place seat counts are enforced".
    - .claude/codingrules.md section 11 for the structured-concurrency rules this module follows
      (no bare `asyncio.create_task`, no busy loop).
    - hivemind.llm.fanner.lane for Fanner and FannerLane, this module's owner and caller.
"""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import Mapping

from hivemind.forage.tempo import AccuracyBar, Tempo
from waggle.clock import Clock

# Lower ranks are admitted first: a CRITICAL- or HIGH-tempo caller jumps ahead of a queued LOW one
# (roadmap step 3.12a: "a queue ordered by tempo for excess requests"), never the reverse.
_TEMPO_PRIORITY: Mapping[AccuracyBar, int] = {
    AccuracyBar.CRITICAL: 0,
    AccuracyBar.HIGH: 1,
    AccuracyBar.NORMAL: 2,
    AccuracyBar.LOW: 3,
}

__all__ = ["SeatMeter"]


class SeatMeter:
    """Meter concurrent calls on one binding against its provider's seat count.

    Owns its own mutable state -- the in-flight count, the waiter heap, the arrival counter --
    under one `asyncio.Lock` (codingrules 8.5): `acquire()` and `release()` both read-then-write
    that state, and two callers racing to grab the last free seat, or to hand a just-released one
    to the next waiter, must never interleave.
    """

    def __init__(self, capacity: int, clock: Clock) -> None:
        """Build a meter with `capacity` concurrent seats.

        Args:
            capacity: How many calls this binding may run at once; the Fanner sizes this from its
                provider's manifest seat count, or `DEFAULT_SEATS`
                (`hivemind.llm.fanner.lane`) when the manifest names none.
            clock: Injected clock, used only to measure how long a queued caller actually waited.
        """
        self._capacity = capacity
        self._clock = clock
        self._in_flight = 0
        # A min-heap of (priority, arrival sequence, event); see the module docstring's "Key
        # invariants" for why the sequence keeps every comparison decidable without touching the
        # Event objects.
        self._waiters: list[tuple[int, int, asyncio.Event]] = []
        self._next_sequence = 0
        self._lock = asyncio.Lock()

    @property
    def capacity(self) -> int:
        """Return how many concurrent calls this binding may run."""
        return self._capacity

    @property
    def in_flight(self) -> int:
        """Return how many calls on this binding are running right now."""
        return self._in_flight

    @property
    def queued(self) -> int:
        """Return how many calls are waiting for a seat on this binding right now."""
        return len(self._waiters)

    async def acquire(self, tempo: Tempo) -> float:
        """Wait for a free seat, ordered by `tempo` ahead of arrival order, and take it.

        Args:
            tempo: The calling lane's tempo; sets this wait's place in the queue.

        Returns:
            How many seconds this call actually waited; 0.0 when a seat was free immediately.
        """
        async with self._lock:
            if self._in_flight < self._capacity:
                # A seat was free: no queueing needed, so there is no wait to measure.
                self._in_flight += 1
                return 0.0
            event = asyncio.Event()
            sequence = self._next_sequence
            self._next_sequence += 1
            heapq.heappush(self._waiters, (_TEMPO_PRIORITY[tempo.accuracy], sequence, event))
        # Waiting on the event, not the lock: release() only holds the lock long enough to hand
        # the seat to the next waiter, so this wait never blocks another caller from enqueueing.
        start_s = self._clock.monotonic()
        await event.wait()
        return self._clock.monotonic() - start_s

    async def release(self) -> None:
        """Free one seat: hand it straight to the highest-priority waiter, or return it idle.

        A released seat is transferred directly to a waiter rather than merely counted back as
        free (see the module docstring's "Key invariants"), so the priority queue's ordering is
        honoured exactly: the waiter already first in line gets the seat, never whichever
        concurrent `acquire()` call happens to run next.
        """
        async with self._lock:
            if self._waiters:
                _, _, event = heapq.heappop(self._waiters)
                event.set()
            else:
                self._in_flight -= 1
