"""Meter one embed call through the Fanner: seat, rate limit, throttle-and-reraise, record.

`hivemind.llm.fanner.lane.FannerLane.embed` is a thin delegate to `embed_through_fanner` here,
split out purely by codingrules 5.1's file-size limit (`lane.py` is already close to it) -- not by
a difference in responsibility, the same reason `hivemind.llm.providers.openai_compat.client`/
`.rate_limit` are split. This module never imports `hivemind.llm.fanner.lane` at runtime, only
under `TYPE_CHECKING` for the `Fanner` type hint: `lane.py` imports *this* module to build
`FannerLane.embed`'s delegate, so the reverse import would cycle. Roadmap 7.1 (ADR-0036) is why
this path is shorter than `FannerLane.complete`'s own: an embed call never spills to
`bound.fallback` on a slow queue or a stale grade the way a chat call does, because a fallback
embedder link only ever exists for the *same* model id, and there is nothing to gain (and vector
comparability to lose) by walking it here instead of leaving that decision to
`hivemind.llm.embedding.gate.EmbedGate`. Two errors change the path mid-call. A
`RateLimitedError` throttles the Forage map source that raised it, exactly like `complete`, and
re-raises unchanged. A `ProviderUnavailableError` moves to `bound.fallback` when there is one, the
same walk `hivemind.llm.embedding.gate.DirectEmbedGate` makes: the `EmbedGate` contract this lane
implements promises that every same-model fallback was tried before the caller sees an outage.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.fanner`. Called only by
    `hivemind.llm.fanner.lane.FannerLane.embed`. Calls into `hivemind.forage.tempo`,
    `hivemind.llm.embedding`, `hivemind.llm.errors` and `waggle.ids` only.

Key invariants:
    - Never spills on a slow queue or a stale grade; `bound.fallback` is taken only when the
      provider is unreachable (`ProviderUnavailableError`), and a fallback link always serves the
      same model id (`hivemind.llm.registry.ProviderRegistry.embedder` cuts any other).
    - The seat is always released and `call_finished` always fires, success or error alike
      (mirrors `FannerLane._call`'s own `finally`), so a `RateLimitedError` never leaks a held seat.
    - `_LLM_CALL_KIND`/`_LLM_THROTTLED_KIND`/`_DEFAULT_THROTTLE_S` hold the same values as
      `hivemind.llm.fanner.lane`'s own `LLM_CALL_KIND`/`LLM_THROTTLED_KIND`/`DEFAULT_THROTTLE_S`,
      defined again here rather than imported (this module's own docstring: importing `lane.py`
      here would cycle).

See Also:
    - .claude/codingrules.md section 8.10 for the Fanner's role and the two pools it meters.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for "no spill: another model's
      vectors are not comparable" and the metering rule this module implements.
    - hivemind.llm.fanner.lane for FannerLane.embed, this module's one caller.
    - hivemind.llm.fanner.seats and .limiter for SeatMeter and ProviderRateLimiter, metered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from hivemind.forage.tempo import Tempo
from hivemind.llm.embedding.bound import BoundEmbedder
from hivemind.llm.embedding.models import EmbeddingRequest, EmbeddingResponse
from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.llm.fanner.limiter import ProviderRateLimiter
from hivemind.llm.fanner.seats import SeatMeter
from hivemind.llm.models import JsonObject
from waggle.ids import new_event_id

if TYPE_CHECKING:
    # Only for the type hint on `fanner` below; a real import here would cycle with lane.py,
    # which imports this module's embed_through_fanner to build FannerLane.embed's delegate.
    from hivemind.llm.fanner.lane import Fanner

_LLM_CALL_KIND = "llm.call"  # Same event kind as hivemind.llm.fanner.lane.LLM_CALL_KIND: an
# embed call is metered the same way a chat call is, only the recorded slot differs.
_LLM_THROTTLED_KIND = "llm.throttled"  # Same kind as hivemind.llm.fanner.lane.LLM_THROTTLED_KIND.
_DEFAULT_THROTTLE_S = 60.0  # Same value as hivemind.llm.fanner.lane.DEFAULT_THROTTLE_S.
_CHARS_PER_TOKEN_ESTIMATE = 4  # roadmap 7.1: "estimated tokens = total chars / 4".

__all__ = ["embed_through_fanner"]


@dataclass(frozen=True, slots=True)
class _Meter:
    """One embed attempt's resolved collaborators, keeping every helper within the 5-param limit."""

    source_id: str | None
    seat_meter: SeatMeter
    rate_limiter: ProviderRateLimiter
    estimated_tokens: int


async def embed_through_fanner(
    fanner: Fanner, tempo: Tempo, bound: BoundEmbedder, request: EmbeddingRequest
) -> EmbeddingResponse:
    """Meter one embed call through `fanner`, walking same-model fallbacks on an outage.

    See `FannerLane.embed` for the full contract; this is that method's whole body.

    Args:
        fanner: The Fanner whose seat meter, rate limiter, map and recorder to use.
        tempo: The calling lane's tempo; orders this call in the provider's seat queue.
        bound: The binding to call first; `bound.fallback` only on `ProviderUnavailableError`.
        request: The texts to embed.

    Returns:
        The completed EmbeddingResponse.

    Raises:
        Whatever the last link's `provider.embed` raises, unchanged, after a `RateLimitedError`
        has already throttled the map source it came from.
    """
    current = bound
    while True:
        try:
            return await _embed_once(fanner, tempo, current, request)
        except ProviderUnavailableError:
            if current.fallback is None:
                raise  # Every same-model link is down; the caller degrades (ADR-0036).
            current = current.fallback


async def _embed_once(
    fanner: Fanner, tempo: Tempo, bound: BoundEmbedder, request: EmbeddingRequest
) -> EmbeddingResponse:
    """Meter one embed call on exactly `bound`: seat, rate limit, throttle-and-reraise, record."""
    provider = bound.provider.name
    meter = await _prepare(fanner, provider, bound.model, request)
    # External wait: throttled by this provider's own manifest rate limit, mirrors
    # FannerLane._meter's own call for a chat completion.
    await meter.rate_limiter.acquire(meter.estimated_tokens)
    # External wait: queueing for a seat, ordered by this lane's tempo; no queue-wait spill check
    # here (module docstring: embeddings never spill), unlike FannerLane._meter's own chat path.
    await meter.seat_meter.acquire(tempo)
    start_s = fanner.deps.clock.monotonic()
    await fanner.deps.recorder.call_started(meter.source_id, provider)
    try:
        # External await: the actual embedding call; the provider adapter owns its own timeout.
        response = await bound.provider.embed(request)
    except RateLimitedError as exc:
        # The provider said "wait": mask its headroom on the map and record the discovery before
        # re-raising -- embeddings never spill to a fallback, so there is nothing else to try.
        await _throttle(fanner, bound, meter.source_id, exc.retry_after_s)
        raise
    finally:
        # Always release, success or error, so a raised exception never leaks a held seat.
        await meter.seat_meter.release()
        await fanner.deps.recorder.call_finished(meter.source_id, provider)
    latency_s = fanner.deps.clock.monotonic() - start_s
    actual_tokens = response.usage.input_tokens + response.usage.output_tokens
    meter.rate_limiter.observe_actual_tokens(meter.estimated_tokens, actual_tokens)
    await _record_success(fanner, bound, response, latency_s)
    return response


async def _prepare(fanner: Fanner, provider: str, model: str, request: EmbeddingRequest) -> _Meter:
    """Resolve `provider`'s seat meter, rate limiter, map source id and this call's estimate."""
    source = fanner.deps.map.find(provider, model)
    return _Meter(
        source_id=source.source_id if source is not None else None,
        seat_meter=await fanner.seat_meter_for(provider),
        rate_limiter=await fanner.rate_limiter_for(provider),
        estimated_tokens=_estimate_tokens(request),
    )


async def _record_success(
    fanner: Fanner, bound: BoundEmbedder, response: EmbeddingResponse, latency_s: float
) -> None:
    """Record the llm.call event for one completed embed call."""
    usage = response.usage
    payload: JsonObject = {
        "slot": bound.slot.value,
        "provider": bound.provider.name,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "cost_usd": usage.cost_usd if usage.cost_usd is not None else 0.0,
        },
        "latency_s": latency_s,
    }
    await fanner.deps.recorder.record(
        kind=_LLM_CALL_KIND, subject_id=new_event_id(fanner.deps.clock), payload=payload
    )


async def _throttle(
    fanner: Fanner, bound: BoundEmbedder, source_id: str | None, retry_after_s: float | None
) -> None:
    """Mask the map source's headroom at zero and record llm.throttled, after a RateLimitedError."""
    wait_s = retry_after_s if retry_after_s is not None else _DEFAULT_THROTTLE_S
    if source_id is not None:
        until = fanner.deps.clock.now() + timedelta(seconds=wait_s)
        await fanner.deps.map.throttle(source_id, until)
    payload: JsonObject = {
        "slot": bound.slot.value,
        "provider": bound.provider.name,
        "source_id": source_id,
        "wait_s": wait_s,
    }
    await fanner.deps.recorder.record(
        kind=_LLM_THROTTLED_KIND, subject_id=new_event_id(fanner.deps.clock), payload=payload
    )


def _estimate_tokens(request: EmbeddingRequest) -> int:
    """Return roadmap 7.1's own estimate: total chars across every text, divided by 4."""
    return sum(len(text) for text in request.texts) // _CHARS_PER_TOKEN_ESTIMATE
