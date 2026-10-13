"""Provide FastClock: real time, with every long sleep cut short so the tests do not wait.

The Pollen Packet paces itself with the injected clock: the reconnection backoff doubles from half
a second, a stop waits fifty-millisecond polls, a heartbeat sleeps ``heartbeat_interval_s``. The
end-to-end tests run a real packet against a real loopback WebSocket, where the work (a process, a
socket, a file) takes real time, so a ``FakeClock`` that never advances by itself would hang them;
yet waiting out half-second backoffs would make the suite crawl. ``FastClock`` reads real wall and
monotonic time and caps each sleep at a few milliseconds, so every loop still yields to the event
loop and still makes progress, only sooner. Tests that need to control time exactly use waggle's
``FakeClock`` instead.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the pollen end-to-end tests through
    conftest.py; satisfies waggle.clock.Clock.

Key invariants:
    - ``now`` and ``monotonic`` are the real ones, so anything that compares them with a
      real-time deadline (a dead-man limit, a timeout) behaves as in production.
    - A sleep is never longer than ``max_sleep_s`` and never skipped: every await still yields.

See Also:
    - waggle.clock for the Clock Protocol and for FakeClock.
"""

from __future__ import annotations

import asyncio

from waggle.clock import SystemClock

__all__ = ["FastClock"]


class FastClock(SystemClock):
    """The system clock whose sleeps are capped at ``max_sleep_s``."""

    def __init__(self, max_sleep_s: float = 0.1) -> None:
        """Choose the cap.

        Args:
            max_sleep_s: The longest a ``sleep`` really waits.
        """
        self._max_sleep_s = max_sleep_s

    async def sleep(self, seconds: float) -> None:
        """Wait for at most ``max_sleep_s``, whatever was asked.

        Args:
            seconds: The nominal wait; only the cap matters.
        """
        await asyncio.sleep(min(seconds, self._max_sleep_s))
