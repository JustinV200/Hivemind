"""Tests for waggle.transport.ceiling: the token bucket behind a connection's frame ceiling.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises FrameCeiling with a FakeClock and no
    I/O: the burst a fresh bucket allows and the refusal after it, the steady rate it refills at,
    the cap on a refill, that a refused frame costs nothing, a wall clock that steps backwards,
    the limits' defaults and validation, and a hypothesis property bounding how many frames any
    schedule can get through.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.ceiling for the module under test.
    - test_websocket_server_limits.py for the ceiling behind a real listener.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from waggle.clock import FakeClock
from waggle.transport.ceiling import (
    BURST_FRAMES,
    MAX_FRAMES_PER_MINUTE,
    CeilingLimits,
    FrameCeiling,
)

PER_MINUTE = 60  # One token a second: round numbers keep the refill arithmetic exact.
BURST = 5


def _ceiling(per_minute: int = PER_MINUTE, burst: int = BURST) -> FrameCeiling:
    """A bucket refilling ``per_minute`` frames a minute, holding at most ``burst``."""
    return FrameCeiling(CeilingLimits(max_frames_per_minute=per_minute, burst=burst))


def _admit_all(ceiling: FrameCeiling, at: datetime, count: int) -> int:
    """Offer ``count`` frames at one instant and return how many were admitted."""
    return sum(ceiling.admit(at) for _ in range(count))


def test_defaults_are_six_thousand_a_minute_with_a_burst_of_six_hundred() -> None:
    assert MAX_FRAMES_PER_MINUTE == 6000
    assert BURST_FRAMES == 600
    assert CeilingLimits() == CeilingLimits(max_frames_per_minute=6000, burst=600)


def test_a_fresh_bucket_admits_a_full_burst_then_refuses(fake_clock: FakeClock) -> None:
    ceiling = _ceiling()
    now = fake_clock.now()

    assert _admit_all(ceiling, now, BURST) == BURST

    assert ceiling.admit(now) is False


def test_the_bucket_refills_at_the_configured_rate(fake_clock: FakeClock) -> None:
    ceiling = _ceiling()  # one token a second
    now = fake_clock.now()
    _admit_all(ceiling, now, BURST)

    # Half a second earns half a token: still nothing. A whole second earns exactly one.
    assert ceiling.admit(now + timedelta(seconds=0.5)) is False
    assert ceiling.admit(now + timedelta(seconds=1.0)) is True
    assert ceiling.admit(now + timedelta(seconds=1.0)) is False
    # Three seconds more, three tokens.
    assert _admit_all(ceiling, now + timedelta(seconds=4.0), 5) == 3


def test_a_refused_frame_spends_nothing(fake_clock: FakeClock) -> None:
    ceiling = _ceiling()
    now = fake_clock.now()
    _admit_all(ceiling, now, BURST)

    # A hammering peer is refused many times in the same second without going into debt: the
    # one token the second earns is still there to spend.
    assert _admit_all(ceiling, now + timedelta(seconds=0.9), 50) == 0
    assert ceiling.admit(now + timedelta(seconds=1.0)) is True


def test_a_long_idle_refills_to_the_burst_and_no_further(fake_clock: FakeClock) -> None:
    ceiling = _ceiling()
    now = fake_clock.now()
    _admit_all(ceiling, now, BURST)

    after_an_hour = now + timedelta(hours=1)

    # The capacity caps the credit: an hour of quiet buys one burst, not three thousand frames.
    assert _admit_all(ceiling, after_an_hour, 10 * BURST) == BURST


def test_a_clock_that_steps_backwards_credits_nothing_and_resumes_from_the_new_reading(
    fake_clock: FakeClock,
) -> None:
    ceiling = _ceiling()
    now = fake_clock.now()
    _admit_all(ceiling, now, BURST)
    earlier = now - timedelta(hours=1)

    # Going back an hour earns nothing, and does not drain what is left either...
    assert ceiling.admit(earlier) is False
    # ...and refilling carries on from the new reading rather than waiting for the old time.
    assert ceiling.admit(earlier + timedelta(seconds=1.0)) is True


def test_the_first_frame_needs_no_start_time(fake_clock: FakeClock) -> None:
    ceiling = _ceiling()

    # A bucket built long before its first frame is still just full: no elapsed time is credited.
    assert ceiling.admit(fake_clock.now() + timedelta(days=30)) is True


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: CeilingLimits(max_frames_per_minute=0), "max_frames_per_minute"),
        (lambda: CeilingLimits(max_frames_per_minute=-1), "max_frames_per_minute"),
        (lambda: CeilingLimits(burst=0), "burst"),
        (lambda: CeilingLimits(burst=-3), "burst"),
    ],
)
def test_limits_that_would_refuse_every_frame_are_refused(
    make: Callable[[], CeilingLimits], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make()


# ──────────────────────────────────────────────────────────────────────────────
# Property
# ──────────────────────────────────────────────────────────────────────────────

_BASE = datetime(2030, 1, 1, tzinfo=UTC)


@settings(max_examples=150, deadline=None)
@given(
    gaps=st.lists(st.floats(min_value=0.0, max_value=3.0), max_size=120),
    per_minute=st.integers(min_value=1, max_value=600),
    burst=st.integers(min_value=1, max_value=20),
)
def test_no_schedule_gets_more_through_than_the_burst_plus_the_refill(
    gaps: list[float], per_minute: int, burst: int
) -> None:
    ceiling = _ceiling(per_minute, burst)
    now = _BASE
    admitted = 0

    # One frame offered after each gap: however the gaps fall, the bucket can only ever have
    # held its burst plus what the elapsed time refilled.
    for gap_s in gaps:
        now += timedelta(seconds=gap_s)
        admitted += ceiling.admit(now)

    elapsed_s = (now - _BASE).total_seconds()
    assert admitted <= burst + elapsed_s * per_minute / 60 + 1e-6
