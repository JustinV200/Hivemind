"""Tests for waggle.loop.TickLoop: the standard long-running loop shape and its backoff.

A ScriptedLoop test double drives each test: its `_tick` follows a list of outcomes ("ok", "fail",
"bad", "wait", "stop") so a test can script exactly the sequence of successes and failures
codingrules section 11's loop shape needs to prove out, all measured on a FakeClock so nothing
here waits on a real timer.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.loop.TickLoop, the base class
    every bee (the Queen, the Hive's central orchestrator; a Warden, a per-Cell supervisor; a
    Worker, a subagent that does the work) and Pollen's own agent loop (the lightweight agent
    that runs on a borrowed device) subclass.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.loop for the module under test.
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for the documented backoff sequence.
"""

from __future__ import annotations

import asyncio

import pytest

from waggle.clock import Clock, FakeClock
from waggle.loop import TickLoop


class ScriptedLoop(TickLoop):
    """A TickLoop whose _tick outcome is scripted step by step, for deterministic tests."""

    _recoverable_errors = (RuntimeError,)

    def __init__(self, clock: Clock, outcomes: list[str]) -> None:
        """Create a ScriptedLoop that plays back `outcomes`, one per call to `_tick`."""
        super().__init__(clock)
        self._outcomes = outcomes
        self.tick_count = 0
        self.failures: list[Exception] = []

    async def _tick(self) -> None:
        outcome = self._outcomes[self.tick_count]
        self.tick_count += 1
        if outcome == "fail":
            raise RuntimeError("boom")
        if outcome == "bad":
            raise ValueError("fatal")
        if outcome == "stop":
            self.stop()
            return
        if outcome == "wait":
            # A never-advanced sleep, so a test can cancel the task while this is suspended.
            await self._clock.sleep(1_000_000)
            return
        # outcome == "ok": succeeds immediately, no suspension.

    async def _on_tick_failed(self, error: Exception) -> None:
        self.failures.append(error)


class RecordingClock(FakeClock):
    """A FakeClock that records every duration passed to sleep(), for asserting backoff values."""

    def __init__(self) -> None:
        """Create a RecordingClock with an empty history of requested sleep durations."""
        super().__init__()
        self.sleep_durations: list[float] = []

    async def sleep(self, seconds: float) -> None:
        """Record `seconds`, then delegate to FakeClock's own sleep."""
        self.sleep_durations.append(seconds)
        await super().sleep(seconds)


async def test_tick_loop_ticks_until_stop() -> None:
    clock = FakeClock()
    loop = ScriptedLoop(clock, ["ok", "ok", "stop"])

    await loop.run()

    assert loop.tick_count == 3


async def test_tick_loop_backs_off_with_the_documented_sequence() -> None:
    clock = RecordingClock()
    loop = ScriptedLoop(clock, ["fail", "fail", "fail", "stop"])

    task = asyncio.ensure_future(loop.run())
    # Each failure suspends on a sleep of the next backoff value; advancing past it lets the
    # loop retry, which then fails again and records the next (doubled) backoff.
    for expected_backoff in (0.5, 1.0, 2.0):
        await asyncio.sleep(0)
        assert clock.sleep_durations[-1] == expected_backoff
        clock.advance(expected_backoff)
    await asyncio.sleep(0)  # lets the final "stop" tick run and the loop exit
    await task

    assert loop.tick_count == 4
    assert len(loop.failures) == 3


async def test_tick_loop_resets_backoff_after_a_successful_tick() -> None:
    clock = RecordingClock()
    loop = ScriptedLoop(clock, ["fail", "fail", "ok", "fail", "stop"])

    task = asyncio.ensure_future(loop.run())
    for expected_backoff in (0.5, 1.0):
        await asyncio.sleep(0)
        assert clock.sleep_durations[-1] == expected_backoff
        clock.advance(expected_backoff)
    # The third tick ("ok") succeeds and the fourth ("fail") runs in the same step, since neither
    # yields control back to the event loop; its backoff should restart at the initial value
    # rather than continuing to double from 2.0.
    await asyncio.sleep(0)
    assert clock.sleep_durations[-1] == 0.5
    clock.advance(0.5)
    await asyncio.sleep(0)
    await task

    assert loop.tick_count == 5


async def test_tick_loop_unrecoverable_error_propagates() -> None:
    clock = FakeClock()
    loop = ScriptedLoop(clock, ["bad"])

    with pytest.raises(ValueError, match="fatal"):
        await loop.run()


async def test_tick_loop_cancellation_propagates() -> None:
    clock = FakeClock()
    loop = ScriptedLoop(clock, ["wait"])

    task = asyncio.ensure_future(loop.run())
    await asyncio.sleep(0)  # let the tick start and suspend on the never-advanced sleep
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_tick_loop_stop_is_idempotent_and_safe_from_another_task() -> None:
    clock = FakeClock()
    loop = ScriptedLoop(clock, ["wait"])

    task = asyncio.ensure_future(loop.run())
    await asyncio.sleep(0)  # let the tick start and suspend on the never-advanced sleep
    loop.stop()
    loop.stop()  # calling stop() twice must not raise
    clock.advance(1_000_000)  # release the wait so the loop can observe the stop flag
    await asyncio.sleep(0)
    await task

    assert loop.tick_count == 1


class _BareLoop(TickLoop):
    """A TickLoop that does not override _on_tick_failed, to exercise the base no-op hook."""

    _recoverable_errors = (RuntimeError,)

    async def _tick(self) -> None:
        """Fail once, then stop, so run() exercises the default _on_tick_failed exactly once."""
        self.stop()
        raise RuntimeError("boom")


async def test_tick_loop_default_on_tick_failed_hook_is_a_no_op() -> None:
    clock = FakeClock()
    loop = _BareLoop(clock)

    task = asyncio.ensure_future(loop.run())
    await asyncio.sleep(0)  # let the failing tick run and suspend on its backoff sleep
    clock.advance(0.5)  # the documented initial backoff (ADR-0003)
    await asyncio.sleep(0)
    await task

    # stop() was already set before the failure, so the loop exits after the one backoff sleep
    # instead of ticking again; reaching here at all proves the default hook did not raise.
