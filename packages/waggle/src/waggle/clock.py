"""Define the Clock protocol every time-reading component depends on, plus its two implementations.

A Clock is the injected source of "what time is it" and "wait this long" that every long-running
part of the Hive uses instead of calling ``datetime.now()`` or ``asyncio.sleep()`` directly. Reading
time through an injected object rather than a global is what lets a test run at simulated speed:
``FakeClock`` never actually sleeps, so a test suite exercising minutes of backoff (see
``waggle.loop``) finishes instantly and deterministically on every host. ``FakeClock`` lives here,
not under ``tests/``, because Pollen (the lightweight agent that runs on a borrowed device) and
``hive doctor`` (the system's built-in diagnostic command) both use it outside of test code
(codingrules section 3).

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Called by waggle.ids (to timestamp new ids) and waggle.loop (to drive backoff sleeps), and by
    every subsystem above them that reads time.

Key invariants:
    - Clock.now() always returns a timezone-aware datetime in UTC; a naive datetime is never
      produced by either implementation.
    - FakeClock.sleep(seconds) only resolves once a caller has advanced the fake clock's time past
      the requested wake time; it never resolves on its own.

See Also:
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for why the clock lives in waggle.
    - waggle.loop for the long-running loop shape built on this Clock.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

# FakeClock needs a deterministic starting point when the caller does not supply one, so two
# tests that both construct FakeClock() with no arguments see the same wall-clock value.
_DEFAULT_FAKE_START = datetime(2020, 1, 1, tzinfo=UTC)

__all__ = ["Clock", "FakeClock", "SystemClock"]


class Clock(Protocol):
    """The time source every component that reads time or sleeps depends on.

    Concurrency model: every method is safe to call from any task on the same event loop.
    ``sleep`` is the only async member; a caller that awaits it is suspended until either real
    time (SystemClock) or an explicit ``advance`` call (FakeClock) satisfies the wait.
    """

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime.

        Returns:
            The current time, always with ``tzinfo`` set to UTC.
        """
        ...

    def monotonic(self) -> float:
        """Return a monotonically non-decreasing count of seconds, for measuring elapsed time.

        Returns:
            Seconds from some fixed but arbitrary reference point; only differences between two
            calls are meaningful.
        """
        ...

    async def sleep(self, seconds: float) -> None:
        """Suspend the caller for approximately ``seconds`` seconds.

        Args:
            seconds: How long to wait. Zero or negative returns immediately.

        Returns:
            None, once the wait is over.
        """
        ...


class SystemClock:
    """The real clock: wall time from ``datetime``, elapsed time from ``time.monotonic``.

    Used by every composition root (a CLI entry point, the Entrance, Pollen's own agent loop) that
    is not itself a test.
    """

    def now(self) -> datetime:
        """Return the current wall-clock time.

        Returns:
            ``datetime.now(UTC)``.
        """
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Return the system's monotonic clock reading.

        Returns:
            ``time.monotonic()``.
        """
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Suspend the caller using the real event loop's sleep.

        Args:
            seconds: How long to wait, in seconds.

        Returns:
            None, once the real wait is over.
        """
        # This is the one legitimate direct asyncio.sleep call; every other component reads time
        # through the injected Clock instead, which is what makes them testable with FakeClock.
        await asyncio.sleep(seconds)


class FakeClock:
    """A clock a test drives by hand: time only moves when ``advance`` is called.

    Concurrency model: ``now`` and ``monotonic`` are plain synchronous reads of the current fake
    time. ``sleep`` registers the caller's wake time and suspends on an ``asyncio.Future``;
    ``advance`` moves the fake clock forward and resolves every future whose wake time has passed,
    earliest first. Nothing here ever waits on a real timer, so a test that calls ``advance``
    controls exactly when a sleeper wakes (codingrules section 14.5: no sleeping in tests).
    """

    def __init__(self, start: datetime | None = None) -> None:
        """Create a FakeClock starting at ``start`` (or a fixed default if omitted).

        Args:
            start: The initial value ``now()`` returns. Defaults to 2020-01-01T00:00:00Z so two
                clocks built with no argument agree.
        """
        self._now = start if start is not None else _DEFAULT_FAKE_START
        self._monotonic = 0.0
        # Pending sleepers as (wake_at monotonic time, insertion sequence, future to resolve);
        # advance() drains this. The sequence number is a tiebreaker only: two sleeps registered
        # for the exact same wake_at (a common case when several collaborators share one interval,
        # e.g. two heartbeat cadences both 0.05s and both armed at monotonic 0.0) would otherwise
        # make advance()'s own sort compare two asyncio.Future objects directly and raise TypeError
        # (neither Future nor the tuple itself defines ordering past its first differing element).
        self._pending: list[tuple[float, int, asyncio.Future[None]]] = []
        self._next_sequence = 0

    def now(self) -> datetime:
        """Return the fake clock's current wall-clock time.

        Returns:
            The value set at construction, plus every second passed to ``advance`` so far.
        """
        return self._now

    def monotonic(self) -> float:
        """Return the fake clock's current monotonic reading.

        Returns:
            Seconds advanced so far, starting from 0.0 at construction.
        """
        return self._monotonic

    async def sleep(self, seconds: float) -> None:
        """Suspend until a test calls ``advance`` past this sleep's wake time.

        Args:
            seconds: How long to wait, in fake seconds. Zero or negative returns immediately
                without registering a wake time.

        Returns:
            None, once ``advance`` has moved the fake clock's monotonic time past
            ``monotonic() + seconds`` as it stood when this was called.
        """
        # A non-positive duration has already "elapsed"; returning immediately avoids leaving a
        # future in _pending that would never legitimately expire on its own.
        if seconds <= 0:
            return
        wake_at = self._monotonic + seconds
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._next_sequence += 1
        self._pending.append((wake_at, self._next_sequence, future))
        await future

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward and wake every sleeper whose deadline has passed.

        Args:
            seconds: How many seconds to add to both ``now()`` and ``monotonic()``. Must be
                positive.

        Returns:
            None.

        Raises:
            ValueError: ``seconds`` is not positive.
        """
        if seconds <= 0:
            raise ValueError(f"advance requires a positive number of seconds, got {seconds}")
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds

        # Wake every sleeper whose deadline is now in the past, earliest deadline first, so
        # sleepers with shorter durations observably resume before ones with longer durations
        # even when a single advance() call satisfies several at once; the insertion sequence
        # breaks a tie between two sleepers that share the exact same wake_at (registration order,
        # never comparing the two Futures directly -- see __init__'s own comment on _pending).
        due = sorted(
            (wake_at, sequence, future)
            for wake_at, sequence, future in self._pending
            if wake_at <= self._monotonic
        )
        self._pending = [
            (wake_at, sequence, future)
            for wake_at, sequence, future in self._pending
            if wake_at > self._monotonic
        ]
        for _, _, future in due:
            if not future.done():
                future.set_result(None)
