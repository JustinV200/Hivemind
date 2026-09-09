"""Define RateLimit and ProviderRateLimiter: the Fanner's per-provider request/token throttle.

Codingrules section 8.10: the Fanner is "a rate limiter per hosted provider" as well as a seat
meter. `RateLimit` is the frozen shape of one provider's manifest row (`[llm.providers.<name>]`'s
`requests_per_minute`/`tokens_per_minute`); `ProviderRateLimiter` is a token-bucket throttle built
from one, so a burst of calls against a hosted provider's own published rate limit waits instead of
being rejected -- the Fanner never refuses a call (roadmap step 3.12a), it only ever makes one wait.
`estimate_tokens` is the rough, pre-call guess `ProviderRateLimiter.acquire` reserves capacity
against; `observe_actual_tokens` corrects that guess once the real `Usage` is known, so a request
type that is chronically over- or under-estimated does not slowly starve (or over-admit against)
the token bucket.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.fanner`. Called by
    `hivemind.llm.fanner.lane.FannerLane.complete`, once per binding attempt, before it queues for
    a seat. Calls into `hivemind.llm.models` (for the token estimate) and `waggle.clock` only.

Key invariants:
    - A `ProviderRateLimiter`'s buckets start full: the very first call on a fresh limiter never
      waits (roadmap step 3.12a's own test list: "a fresh bucket does not wait").
    - `requests_per_minute`/`tokens_per_minute` of None means that dimension is unlimited; a
      `RateLimit` with both None (the default, and what a provider absent from `deps.limits` gets)
      never makes `acquire()` wait.
    - `acquire()` only ever waits via the injected `Clock.sleep`, never `asyncio.sleep` directly
      (codingrules section 11), so a test drives it deterministically with `FakeClock`.

See Also:
    - .claude/codingrules.md section 8.10 for "a rate limiter per hosted provider".
    - .claude/codingrules.md section 11 for the Clock-only-sleep rule this module follows.
    - hivemind.llm.fanner.lane for FannerLane, this module's one caller.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm.models import LLMRequest, TextPart
from waggle.clock import Clock

_SECONDS_PER_MINUTE = 60.0  # RateLimit's counts are per-minute; a bucket refills on this period.
_CHARS_PER_TOKEN_ESTIMATE = 4  # A rough, provider-agnostic guess (mirrors hivemind.llm.fake's own
# CHARS_PER_TOKEN_ESTIMATE convention, kept as a separate constant since this module may not
# import a fake from outside its own package for a real, shipped estimate).

__all__ = ["ProviderRateLimiter", "RateLimit", "estimate_tokens"]


class RateLimit(BaseModel):
    """One provider's requests/minute and tokens/minute ceiling, from its manifest row.

    Both fields are None on a provider the manifest's `[llm.providers.<name>]` table leaves
    unset, which `ProviderRateLimiter` reads as "unlimited" on that dimension.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    requests_per_minute: int | None = Field(
        default=None,
        ge=1,
        description="Requests/minute this provider allows; None for no request-rate cap.",
    )
    tokens_per_minute: int | None = Field(
        default=None,
        ge=1,
        description="Tokens/minute this provider allows; None for no token-rate cap.",
    )


def estimate_tokens(request: LLMRequest) -> int:
    """Estimate how many tokens `request` will use, before the real Usage is known.

    Args:
        request: The call about to be made.

    Returns:
        A rough token count: the system prompt's and every text turn's character count, divided
        by `_CHARS_PER_TOKEN_ESTIMATE`, plus `request.max_output_tokens` as the anticipated
        output. Only good enough to size a rate-limit wait; `ProviderRateLimiter.
        observe_actual_tokens` corrects the bucket from the real Usage once the call returns.
    """
    system_chars = len(request.system) if request.system is not None else 0
    message_chars = sum(
        len(part.text)
        for message in request.messages
        for part in message.parts
        if isinstance(part, TextPart)
    )
    input_estimate = (system_chars + message_chars) // _CHARS_PER_TOKEN_ESTIMATE
    return input_estimate + request.max_output_tokens


class ProviderRateLimiter:
    """Throttle one provider's calls to its RateLimit, using two token buckets over a Clock.

    Owns its own mutable bucket state (codingrules 8.5): `acquire` reads and writes the same
    figures every call on this provider shares, so both the refill and the wait decision run
    under one `asyncio.Lock` rather than risking two concurrent callers computing a wait from the
    same stale reading.
    """

    def __init__(self, limit: RateLimit, clock: Clock) -> None:
        """Build a limiter for one provider, its buckets starting full.

        Args:
            limit: The provider's manifest-configured ceiling.
            clock: Injected clock; `monotonic()` drives refill, `sleep()` drives any wait.
        """
        self._limit = limit
        self._clock = clock
        self._available_requests = _as_float(limit.requests_per_minute)
        self._available_tokens = _as_float(limit.tokens_per_minute)
        self._last_refill_s = clock.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int) -> None:
        """Wait, if needed, until both buckets can afford one request of `estimated_tokens`.

        Args:
            estimated_tokens: This call's `estimate_tokens` guess.
        """
        async with self._lock:
            self._refill()
            wait_s = self._wait_needed_s(estimated_tokens)
            if wait_s > 0:
                # External wait: throttled by this provider's own manifest rate limit. The
                # Fanner never refuses a call (roadmap step 3.12a), only waits, so there is no
                # timeout on this sleep.
                await self._clock.sleep(wait_s)
                self._refill()
            self._consume(estimated_tokens)

    def observe_actual_tokens(self, estimated_tokens: int, actual_tokens: int) -> None:
        """Correct the token bucket for the gap between `estimated_tokens` and what really ran.

        Args:
            estimated_tokens: What `acquire` was told to reserve for this call.
            actual_tokens: The call's real total token count, from its Usage.
        """
        tokens_per_minute = self._limit.tokens_per_minute
        if self._available_tokens is None or tokens_per_minute is None:
            return
        cap = float(tokens_per_minute)
        corrected = self._available_tokens + (estimated_tokens - actual_tokens)
        self._available_tokens = min(max(corrected, 0.0), cap)

    def _refill(self) -> None:
        """Add back capacity for the time elapsed since the last refill, capped at each ceiling."""
        now_s = self._clock.monotonic()
        elapsed_s = now_s - self._last_refill_s
        self._last_refill_s = now_s
        if elapsed_s <= 0:
            # A zero (or, under a fake clock that has not advanced, unchanged) elapsed time has
            # nothing to refill; guards against a spurious refill on the very first acquire().
            return
        requests_per_minute = self._limit.requests_per_minute
        if self._available_requests is not None and requests_per_minute is not None:
            rate = requests_per_minute / _SECONDS_PER_MINUTE
            self._available_requests = min(
                self._available_requests + elapsed_s * rate, float(requests_per_minute)
            )
        tokens_per_minute = self._limit.tokens_per_minute
        if self._available_tokens is not None and tokens_per_minute is not None:
            rate = tokens_per_minute / _SECONDS_PER_MINUTE
            self._available_tokens = min(
                self._available_tokens + elapsed_s * rate, float(tokens_per_minute)
            )

    def _wait_needed_s(self, estimated_tokens: int) -> float:
        """Return how long to wait for both buckets to afford one call, 0.0 if neither is short."""
        wait_s = 0.0
        requests_per_minute = self._limit.requests_per_minute
        if (
            self._available_requests is not None
            and requests_per_minute is not None
            and self._available_requests < 1.0
        ):
            rate = requests_per_minute / _SECONDS_PER_MINUTE
            wait_s = max(wait_s, (1.0 - self._available_requests) / rate)
        tokens_per_minute = self._limit.tokens_per_minute
        if (
            self._available_tokens is not None
            and tokens_per_minute is not None
            and self._available_tokens < estimated_tokens
        ):
            rate = tokens_per_minute / _SECONDS_PER_MINUTE
            wait_s = max(wait_s, (estimated_tokens - self._available_tokens) / rate)
        return wait_s

    def _consume(self, estimated_tokens: int) -> None:
        """Deduct one request and `estimated_tokens` from whichever buckets are metered."""
        if self._available_requests is not None:
            self._available_requests -= 1.0
        if self._available_tokens is not None:
            self._available_tokens -= estimated_tokens


def _as_float(value: int | None) -> float | None:
    """Return `value` as a float, or None unchanged."""
    return float(value) if value is not None else None
