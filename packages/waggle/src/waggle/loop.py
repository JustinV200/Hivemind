"""Define TickLoop, the standard long-running loop shape every bee and Pollen subclass.

The Queen (the Hive's central orchestrator), every Warden (a per-Cell supervisor), every Worker
(a subagent that does the work) and the Pollen gateway (the lightweight agent that runs on a
borrowed device) all run forever, waking up to do one unit of work, until something tells them to
stop. Codingrules section 11 requires all of them to follow the same documented shape instead of
each hand-rolling their own retry and backoff logic; this module is that shape, written once here
because ``waggle`` is the one package usable by both ``hivemind`` and Pollen (which may depend on
nothing else in the workspace).

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Subclassed by hivemind.queen.Queen, every hivemind.wardens.Warden, every
    hivemind.workers.Worker and pollen's own agent loop. Calls into waggle.clock for every sleep.

Key invariants:
    - run() loops calling _tick() until stop() has been called; it never returns while the stop
      flag is clear.
    - asyncio.CancelledError is always re-raised, never treated as a recoverable error.
    - Backoff after a recoverable error starts at INITIAL_BACKOFF_S, doubles per consecutive
      failure up to MAX_BACKOFF_S, and resets to INITIAL_BACKOFF_S after the next successful tick.
    - Every sleep this loop performs, including backoff, goes through the injected Clock, never
      asyncio.sleep directly, so tests can drive it with a FakeClock.

See Also:
    - .claude/codingrules.md section 11 for the loop shape this class implements.
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for why this lives in waggle.
    - waggle.clock for the Clock protocol this loop is driven by.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import ClassVar

from waggle.clock import Clock

INITIAL_BACKOFF_S = 0.5  # First wait after a recoverable failure (ADR-0003's documented sequence).
BACKOFF_FACTOR = 2.0  # Each consecutive failure's wait doubles the previous one.
MAX_BACKOFF_S = 30.0  # Backoff never grows past this, so recovery is retried at least every 30s.

__all__ = ["TickLoop"]


class TickLoop(ABC):
    """Run ``_tick()`` forever, with capped exponential backoff on recoverable errors.

    A subclass overrides ``_tick`` with one iteration's work, declares which exceptions are
    recoverable via the ``_recoverable_errors`` class attribute, and may override
    ``_on_tick_failed`` to record a failure (a Warden overrides it to write a PheromoneEvent, an
    entry on the Pheromone Trail, the Hive's append-only audit log; this base class knows nothing
    about the Trail, since waggle may not import hivemind). Any exception not listed in
    ``_recoverable_errors`` propagates out of ``run()`` and ends the loop.
    """

    #: Exception types a subclass considers recoverable; anything else propagates. Empty by
    #: default, meaning no error is recoverable until a subclass says otherwise.
    _recoverable_errors: ClassVar[tuple[type[Exception], ...]] = ()

    def __init__(self, clock: Clock) -> None:
        """Create a TickLoop driven by ``clock`` for every backoff sleep.

        Args:
            clock: Injected time source (SystemClock in production, FakeClock in tests) so
                backoff sleeps are deterministic to test.
        """
        self._clock = clock
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Call ``_tick()`` repeatedly until ``stop()``, backing off on recoverable errors.

        Returns:
            None, once ``stop()`` has been called and the current tick (if any) has finished.

        Raises:
            Exception: Any exception ``_tick`` raises that is not listed in
                ``_recoverable_errors``; the loop does not catch it.
        """
        backoff = INITIAL_BACKOFF_S  # Reset at the top so each run() starts from the initial wait.
        while not self._stop.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                # Cancellation is how a supervisor's own task group tears this loop down; it must
                # never be mistaken for a recoverable failure.
                raise
            except self._recoverable_errors as error:
                # The hook is awaited because the real overrides write to a store (the trail is
                # async), and a sync hook would force them to spawn an unowned task to do so.
                await self._on_tick_failed(error)
                # Every sleep here goes through the injected Clock so a test can advance it
                # instead of waiting on a real timer (codingrules section 14.5).
                await self._clock.sleep(backoff)
                backoff = min(backoff * BACKOFF_FACTOR, MAX_BACKOFF_S)
                continue
            # A tick that did not raise is a success: the next failure, if any, starts backing
            # off from the initial wait again rather than continuing from wherever it left off.
            backoff = INITIAL_BACKOFF_S

    def stop(self) -> None:
        """Ask the loop to stop after its current tick finishes.

        Idempotent and safe to call from any task: it only sets an ``asyncio.Event``, which is
        itself safe to set more than once or from a task other than the one running ``run()``.

        Returns:
            None.
        """
        self._stop.set()

    @abstractmethod
    async def _tick(self) -> None:
        """Perform one iteration of this loop's work; overridden by every subclass."""
        raise NotImplementedError

    async def _on_tick_failed(self, error: Exception) -> None:
        """Record a recoverable tick failure; overridden by a subclass with somewhere to log it.

        The base implementation does nothing: waggle knows nothing about the Pheromone Trail (the
        Hive's append-only audit log). A hivemind subclass overrides this to write a
        PheromoneEvent before returning.

        Args:
            error: The recoverable exception ``_tick`` raised.
        """
        return None
