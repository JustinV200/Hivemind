"""Tests for hivemind.llm.fanner.limiter: RateLimit, ProviderRateLimiter and estimate_tokens.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/limiter.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.limiter for the module under test.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.llm import make_request
from pydantic import ValidationError

from hivemind.llm.fanner.limiter import ProviderRateLimiter, RateLimit, estimate_tokens
from waggle.clock import FakeClock


def test_rate_limit_defaults_to_unlimited_on_both_dimensions() -> None:
    limit = RateLimit()

    assert limit.requests_per_minute is None
    assert limit.tokens_per_minute is None


def test_rate_limit_is_frozen() -> None:
    limit = RateLimit(requests_per_minute=10)

    with pytest.raises(ValidationError, match="frozen"):
        limit.requests_per_minute = 20  # type: ignore[misc]  # The assignment is the test.


def test_rate_limit_rejects_a_non_positive_ceiling() -> None:
    with pytest.raises(ValidationError):
        RateLimit(requests_per_minute=0)


def test_estimate_tokens_combines_input_estimate_and_max_output_tokens() -> None:
    # "Say hello." is 11 chars -> 11 // 4 == 2; system is None, so it contributes nothing.
    request = make_request(max_output_tokens=100)

    assert estimate_tokens(request) == 2 + 100


async def test_acquire_does_not_wait_on_a_fresh_bucket() -> None:
    clock = FakeClock()
    limiter = ProviderRateLimiter(RateLimit(requests_per_minute=60, tokens_per_minute=1_000), clock)

    await limiter.acquire(estimated_tokens=10)

    # No sleep was needed: the fake clock's monotonic time never had to advance.
    assert clock.monotonic() == 0.0


async def test_acquire_never_waits_when_the_limit_is_unset() -> None:
    clock = FakeClock()
    limiter = ProviderRateLimiter(RateLimit(), clock)

    for _ in range(50):
        await limiter.acquire(estimated_tokens=10_000)

    assert clock.monotonic() == 0.0


async def test_acquire_waits_for_the_requests_per_minute_bucket_to_refill() -> None:
    clock = FakeClock()
    limiter = ProviderRateLimiter(RateLimit(requests_per_minute=1), clock)
    await limiter.acquire(estimated_tokens=1)  # Drains the bucket's one available request.

    waiter = asyncio.ensure_future(limiter.acquire(estimated_tokens=1))
    await asyncio.sleep(0)  # Let the waiter reach its own clock.sleep().

    # 1 request/minute means a full 60s wait before the bucket regains its one unit.
    clock.advance(60.0)
    await waiter
    assert clock.monotonic() == 60.0


async def test_acquire_waits_for_the_tokens_per_minute_bucket_to_refill() -> None:
    clock = FakeClock()
    limiter = ProviderRateLimiter(RateLimit(tokens_per_minute=60), clock)
    await limiter.acquire(estimated_tokens=60)  # Drains the token bucket completely.

    waiter = asyncio.ensure_future(limiter.acquire(estimated_tokens=30))
    await asyncio.sleep(0)

    # 60 tokens/minute == 1/second; 30 tokens need 30 seconds to refill.
    clock.advance(30.0)
    await waiter
    assert clock.monotonic() == 30.0


async def test_observe_actual_tokens_corrects_an_overestimate_back_into_the_bucket() -> None:
    clock = FakeClock()
    limiter = ProviderRateLimiter(RateLimit(tokens_per_minute=60), clock)
    await limiter.acquire(estimated_tokens=60)  # Drains the bucket, expecting 60 real tokens.

    limiter.observe_actual_tokens(
        estimated_tokens=60, actual_tokens=10
    )  # Only 10 were really used.

    # 50 tokens were given back; a 50-token request should now need no further wait.
    await limiter.acquire(estimated_tokens=50)
    assert clock.monotonic() == 0.0


async def test_observe_actual_tokens_is_a_no_op_when_the_limit_is_unset() -> None:
    clock = FakeClock()
    limiter = ProviderRateLimiter(RateLimit(), clock)

    limiter.observe_actual_tokens(estimated_tokens=100, actual_tokens=5)  # Does not raise.

    assert clock.monotonic() == 0.0
