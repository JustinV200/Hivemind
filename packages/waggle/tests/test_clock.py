"""Tests for waggle.clock: SystemClock's realness and FakeClock's controllable time.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises SystemClock (a light touch, since it
    wraps the real datetime/time/asyncio) and FakeClock (thoroughly, since every other test in
    this package leans on it for deterministic timing).

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.clock for the module under test.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from waggle.clock import FakeClock, SystemClock


def test_system_clock_now_returns_timezone_aware_utc() -> None:
    clock = SystemClock()

    now = clock.now()

    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(UTC).utcoffset()


def test_system_clock_monotonic_is_non_decreasing() -> None:
    clock = SystemClock()

    first = clock.monotonic()
    second = clock.monotonic()

    # No sleep needed: monotonic() must never go backwards between two calls, however close.
    assert second >= first


async def test_system_clock_sleep_returns_immediately_for_zero_duration() -> None:
    clock = SystemClock()

    # Codingrules 14.5 forbids real waits in tests, so this exercises SystemClock's wiring to
    # asyncio.sleep with a zero duration instead of a timed wait.
    await clock.sleep(0)


def test_fake_clock_starts_at_a_fixed_default_when_no_start_given() -> None:
    clock_a = FakeClock()
    clock_b = FakeClock()

    assert clock_a.now() == clock_b.now()


def test_fake_clock_accepts_an_explicit_start() -> None:
    start = datetime(2030, 6, 1, tzinfo=UTC)

    clock = FakeClock(start=start)

    assert clock.now() == start


def test_fake_clock_advance_moves_now_and_monotonic_forward() -> None:
    clock = FakeClock()
    before_now = clock.now()
    before_monotonic = clock.monotonic()

    clock.advance(5.0)

    assert (clock.now() - before_now).total_seconds() == 5.0
    assert clock.monotonic() - before_monotonic == 5.0


async def test_fake_clock_sleep_resolves_only_after_advance_covers_it() -> None:
    clock = FakeClock()
    # A single-item list, not a bool, so mypy does not narrow the flag to a fixed literal across
    # the nested coroutine's `nonlocal` mutation below.
    woke = [False]

    async def sleeper() -> None:
        await clock.sleep(2.0)
        woke[0] = True

    task = asyncio.ensure_future(sleeper())
    await asyncio.sleep(0)  # let the sleeper register its wake time and suspend
    assert woke == [False]

    clock.advance(1.0)  # not enough yet: the sleeper asked for 2.0 seconds
    await asyncio.sleep(0)
    assert woke == [False]

    clock.advance(1.0)  # now the cumulative advance covers the requested 2.0 seconds
    await asyncio.sleep(0)
    assert woke == [True]

    await task


async def test_fake_clock_advance_wakes_several_sleepers_in_duration_order() -> None:
    clock = FakeClock()
    order: list[int] = []

    async def sleeper(label: int, duration: float) -> None:
        await clock.sleep(duration)
        order.append(label)

    # Start three sleepers with different durations, all before any fake time passes.
    gathered = asyncio.gather(sleeper(1, 3.0), sleeper(2, 1.0), sleeper(3, 2.0))
    await asyncio.sleep(0)  # let each register its wake time

    clock.advance(5.0)  # covers all three at once
    await gathered

    # Shortest duration wakes first, regardless of the order the sleepers were started in.
    assert order == [2, 3, 1]


async def test_fake_clock_sleep_with_non_positive_seconds_returns_immediately() -> None:
    clock = FakeClock()

    # Zero or negative durations have "already elapsed"; sleep() must not register a sleeper
    # that would otherwise sit in _pending forever with nothing left to advance past it.
    await clock.sleep(0.0)
    await clock.sleep(-1.0)


def test_fake_clock_advance_rejects_non_positive_seconds() -> None:
    clock = FakeClock()

    with pytest.raises(ValueError, match="positive"):
        clock.advance(0.0)
