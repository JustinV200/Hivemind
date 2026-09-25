"""Tests for hivemind.entrance.auth.limits.rate: token buckets per device and per address.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/limits/rate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.limits.rate for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.entrance.auth import RateLimiter
from hivemind.manifest import EntranceSection
from hivemind.manifest.schema.entrance import EntranceVoiceSection
from waggle.clock import FakeClock


def test_a_device_gets_a_minutes_allowance_then_its_steady_rate() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock, per_device=60, per_address=30)

    burst = [limiter.allow_device("device-a") for _ in range(61)]
    clock.advance(1)
    refilled = [limiter.allow_device("device-a") for _ in range(2)]

    assert burst == [True] * 60 + [False]
    assert refilled == [True, False]
    assert limiter.allow_device("device-b")


def test_an_address_has_its_own_rate_and_pays_for_invalid_proofs_twice() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock, per_device=60, per_address=30)

    for _ in range(15):
        limiter.charge_address("100.64.0.7")
    allowed = [limiter.allow_address("100.64.0.7") for _ in range(16)]

    assert allowed == [True] * 15 + [False]
    assert limiter.allow_address("100.64.0.8")


def test_memory_stays_bounded_by_evicting_the_least_recently_used_key() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock, per_device=1, per_address=1, capacity=2)
    limiter.allow_device("first")
    limiter.allow_device("second")
    spent_while_kept = limiter.allow_device("second")

    limiter.allow_device("third")

    assert not spent_while_kept
    # "first" was evicted, so it starts again with a full bucket; "third" was just used.
    assert limiter.allow_device("first")
    assert not limiter.allow_device("third")


def test_the_limits_come_from_the_manifest() -> None:
    clock = FakeClock()
    limiter = RateLimiter.from_section(
        EntranceSection(rate_limit_per_device=2, rate_limit_per_address=1), clock
    )

    assert [limiter.allow_device("d") for _ in range(3)] == [True, True, False]
    assert [limiter.allow_address("a") for _ in range(2)] == [True, False]


@pytest.mark.parametrize(("device", "address", "capacity"), [(0, 1, 1), (1, 0, 1), (1, 1, 0)])
def test_a_rate_or_capacity_below_one_is_refused(device: int, address: int, capacity: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RateLimiter(FakeClock(), device, address, capacity)


def test_a_devices_audio_is_charged_in_seconds_whole_clips_or_nothing() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock, per_device=60, per_address=30, audio_s_per_device=120.0)

    first = limiter.allow_audio("device-a", 100.0)
    too_long = limiter.allow_audio("device-a", 30.0)  # Only 20 s left: refused whole.
    fits = limiter.allow_audio("device-a", 20.0)
    clock.advance(30)  # Half a minute refills 60 s of audio.
    refilled = limiter.allow_audio("device-a", 60.0)

    assert (first, too_long, fits, refilled) == (True, False, True, True)
    assert limiter.allow_audio("device-b", 120.0)  # Every device has its own budget.


def test_audio_seconds_are_a_budget_apart_from_the_request_rate() -> None:
    limiter = RateLimiter(FakeClock(), per_device=1, per_address=1, audio_s_per_device=10.0)

    spent_request = limiter.allow_device("device-a")
    audio = limiter.allow_audio("device-a", 10.0)

    assert spent_request and audio
    assert not limiter.allow_device("device-a")
    assert not limiter.allow_audio("device-a", 0.5)


def test_the_audio_budget_comes_from_the_voice_section() -> None:
    voice = EntranceVoiceSection(max_clip_seconds=5.0, audio_seconds_per_minute=6.0)
    limiter = RateLimiter.from_section(EntranceSection(voice=voice), FakeClock())

    assert [limiter.allow_audio("d", 3.0) for _ in range(3)] == [True, True, False]


def test_an_audio_budget_that_is_not_positive_is_refused() -> None:
    with pytest.raises(ValueError, match="audio budget"):
        RateLimiter(FakeClock(), 1, 1, audio_s_per_device=0.0)
