"""Define Fanner and FannerLane: the seat meter every model call in the Hive passes through.

Named after the bees that fan their wings to regulate the hive's temperature and airflow
(codingrules 6.1's "Fanner" row): the Fanner is "the only place seat counts are enforced, so a
grant is a fact rather than a suggestion" (codingrules section 8.10). `Fanner` owns the shared
per-provider `hivemind.llm.fanner.seats.SeatMeter`s and
`hivemind.llm.fanner.limiter.ProviderRateLimiter`s every call in the Hive process shares;
`Fanner.lane(tempo)` hands one bee or call site a `FannerLane`, which implements
`hivemind.llm.ladders.gate.CallGate` exactly, so a degradation ladder (`complete_structured`,
`run_tool_loop`) can take a lane as its `gate=` with no code of its own aware the Fanner exists
(ADR-0009: "this is the seam the Fanner will occupy once it exists").

`FannerLane.complete` walks `bound`'s fallback chain one binding at a time. For each binding it
first checks whether the Forage map's live figures already justify a spill (the binding's grade is
below the calling tempo's floor, or the model is not loaded there --
`hivemind.llm.fanner.spill.static_spill_reason`); if not, it rate-limits per provider
(`hivemind.llm.fanner.limiter`), then queues for a seat on the binding's provider
(`hivemind.llm.fanner.seats.SeatMeter`, ordered by tempo); if that queueing itself ate too much of
the tempo's latency budget (`hivemind.llm.fanner.spill.queue_wait_exceeded`), it also spills. Every
spill is recorded as an `llm.spill` event; every completed call is recorded as an `llm.call` event,
carrying the normalised `Usage` and the measured latency, and (when the source is known) updates
the Forage map's `distance` and `abundance` so the next call's routing sees fresh figures. With no
fallback left, `FannerLane.complete` proceeds on the current binding regardless: the Fanner never
refuses a call (codingrules section 8.10); a ladder's own retry and fallback logic, and the
escalation policy above it, are what decide whether to give up.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm`. Called by every
    ladder and, once wired at the composition root, every awake episode and Worker tool loop that
    makes a model call. Calls into `hivemind.forage` (the map, `ModelSource` and `Tempo`),
    `hivemind.llm`'s own boundary (`BoundModel`, `LLMRequest`, `LLMResponse`) and this package's
    sibling modules (`seats`, `limiter`, `spill`, `recorder`) only; no vendor SDK and no provider
    code beyond the `LLMProvider` protocol.

Key invariants:
    - `Fanner` meters per provider (`BoundModel.provider.name`, the manifest
      `[llm.providers.<name>]` key), never per binding: two bindings on the same provider share
      one `SeatMeter`, because seats are that provider's concurrency budget (a hosted API's
      tier, a local server's parallel slots) and a second manifest row does not buy a second
      budget. Per-source `SeatReservation`s from a grant refine this in phase 4.
    - A seat is always released, success or error (`FannerLane._call`'s `finally`): a provider
      error propagates unchanged to the caller, never swallowed here, but never leaks a held seat.
    - `Fanner` never refuses a call: with no fallback left, `FannerLane.complete` makes the call on
      the current binding regardless of any spill reason it found.

See Also:
    - .claude/codingrules.md section 8.10 for the Fanner's role, spill-over and the two pools.
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the CallGate seam
      this lane implements.
    - docs/adr/0015-forage-map-seats-footprints-and-the-fanner.md for the map/Fanner split this
      module's `ForageMap.observe`/`set_abundance` calls close.
    - hivemind.llm.fanner.seats, .limiter, .spill, .recorder for this module's collaborators.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelSource
from hivemind.forage.tempo import Tempo
from hivemind.llm.fanner.limiter import ProviderRateLimiter, RateLimit, estimate_tokens
from hivemind.llm.fanner.recorder import LlmEventRecorder
from hivemind.llm.fanner.seats import SeatMeter
from hivemind.llm.fanner.spill import SpillReason, queue_wait_exceeded, static_spill_reason
from hivemind.llm.models import JsonObject, LLMRequest, LLMResponse
from hivemind.llm.slots import BoundModel
from waggle.clock import Clock
from waggle.ids import new_event_id

DEFAULT_SEATS = 1  # A provider absent from `FannerDeps.seats`: one request at a time is the only
# safe assumption (roadmap step 3.12a's own constant name and value).
LLM_CALL_KIND = "llm.call"  # The LlmEvent kind FannerLane records for a completed call.
LLM_SPILL_KIND = "llm.spill"  # The LlmEvent kind FannerLane records for a spill.
_LATENCY_EPSILON_S = 0.001  # Floors a near-zero measured latency so tokens/s never divides by 0.

__all__ = [
    "DEFAULT_SEATS",
    "LLM_CALL_KIND",
    "LLM_SPILL_KIND",
    "Fanner",
    "FannerDeps",
    "FannerLane",
]


@dataclass(frozen=True, slots=True)
class FannerDeps:
    """The Fanner's fixed collaborators: everything it needs but never constructs itself.

    Built once by the composition root from the Hive Manifest and the Forage map; the Fanner
    itself never imports `hivemind.manifest` (codingrules section 4: `llm` may not import
    `manifest`, a Layer 1 sibling it sits beside, not below).
    """

    map: ForageMap  # Every known model source; read for spill decisions, written after a call.
    seats: Mapping[str, int]  # Provider name -> seat count, from [llm.providers.<name>] seats.
    limits: Mapping[str, RateLimit]  # Provider name -> its RateLimit; absent means unlimited.
    clock: Clock  # Drives every wait and every recorded event's timestamp.
    recorder: LlmEventRecorder  # Where a completed call or a spill is reported.


@dataclass(frozen=True, slots=True)
class _Attempt:
    """Group one binding attempt's collaborators, keeping helper methods within 5 params."""

    bound: BoundModel
    source: ModelSource | None
    seat_meter: SeatMeter
    rate_limiter: ProviderRateLimiter
    estimated_tokens: int


class Fanner:
    """Own every provider's SeatMeter and ProviderRateLimiter for one Hive process.

    Codingrules section 8.10: "the only place seat counts are enforced, so a grant is a fact
    rather than a suggestion." One Fanner is shared by every bee in the process; `lane()` hands
    each bee or call site its own `FannerLane` over the same shared meters and limiters.
    """

    def __init__(self, deps: FannerDeps) -> None:
        """Build a Fanner with no meters or limiters created yet; they are built on first use.

        Args:
            deps: The Fanner's fixed collaborators.
        """
        self._deps = deps
        self._seat_meters: dict[str, SeatMeter] = {}
        self._rate_limiters: dict[str, ProviderRateLimiter] = {}
        # Guards creating a provider's SeatMeter or ProviderRateLimiter, so two
        # concurrent first calls on the same key never build (and silently orphan) two of them.
        self._creation_lock = asyncio.Lock()

    @property
    def deps(self) -> FannerDeps:
        """Return this Fanner's collaborators, for its own FannerLanes to call back into."""
        return self._deps

    def lane(self, tempo: Tempo) -> FannerLane:
        """Build a FannerLane for one bee or call site, ordered in every queue by `tempo`.

        Args:
            tempo: The calling bee's speed-against-accuracy setting; orders this lane's calls in
                every SeatMeter queue and sets its spill threshold.

        Returns:
            A `hivemind.llm.ladders.gate.CallGate` a ladder or a bee can call `complete` on
            directly, or pass as a ladder's `gate=`.
        """
        return FannerLane(self, tempo)

    def in_flight(self, provider: str) -> int:
        """Return how many calls on `provider` are running right now.

        Args:
            provider: A manifest `[llm.providers.<name>]` key.

        Returns:
            0 if `provider` has never been called through this Fanner yet.
        """
        meter = self._seat_meters.get(provider)
        return meter.in_flight if meter is not None else 0

    def queued(self, provider: str) -> int:
        """Return how many calls are waiting for a seat on `provider` right now.

        Args:
            provider: A manifest `[llm.providers.<name>]` key.
        """
        meter = self._seat_meters.get(provider)
        return meter.queued if meter is not None else 0

    async def seat_meter_for(self, provider: str) -> SeatMeter:
        """Return `provider`'s SeatMeter, creating it sized to its manifest seats on first use.

        Args:
            provider: The manifest `[llm.providers.<name>]` key whose seat count is the budget
                every binding on that provider shares (see the module docstring's "Key
                invariants").
        """
        async with self._creation_lock:
            meter = self._seat_meters.get(provider)
            if meter is None:
                capacity = self._deps.seats.get(provider, DEFAULT_SEATS)
                meter = SeatMeter(capacity, self._deps.clock)
                self._seat_meters[provider] = meter
            return meter

    async def rate_limiter_for(self, provider: str) -> ProviderRateLimiter:
        """Return `provider`'s ProviderRateLimiter, creating one from its manifest limit if needed.

        Args:
            provider: The manifest `[llm.providers.<name>]` key to throttle.
        """
        async with self._creation_lock:
            limiter = self._rate_limiters.get(provider)
            if limiter is None:
                limit = self._deps.limits.get(provider, RateLimit())
                limiter = ProviderRateLimiter(limit, self._deps.clock)
                self._rate_limiters[provider] = limiter
            return limiter


class FannerLane:
    """One caller's CallGate onto the Fanner: metering, spill-over and event recording.

    Implements `hivemind.llm.ladders.gate.CallGate` exactly (structurally, per codingrules 8.1's
    Protocol convention -- no explicit subclassing needed), so a ladder can take a lane as its
    `gate=` with no code of its own aware the Fanner exists.
    """

    def __init__(self, fanner: Fanner, tempo: Tempo) -> None:
        """Bind this lane to its owning Fanner and the tempo that orders its queued calls.

        Args:
            fanner: The Fanner whose seat meters, rate limiters, map and recorder this lane
                shares with every other lane on the same Hive process.
            tempo: This lane's speed-against-accuracy setting; sets its place in every seat queue
                and its spill threshold (`hivemind.llm.fanner.spill.SPILL_WAIT_FRACTION` of its
                latency budget).
        """
        self._fanner = fanner
        self._tempo = tempo

    async def complete(self, bound: BoundModel, request: LLMRequest) -> LLMResponse:
        """Run `request` through `bound`'s chain, metering and spilling as needed.

        See the module docstring for the full walk. Args and Returns mirror
        `hivemind.llm.ladders.gate.CallGate.complete` exactly.

        Raises:
            Whatever `current.provider.complete` raises, unchanged: the Fanner meters and spills,
            it never retries and never converts a provider error (that is a ladder's job).
        """
        current = bound
        while True:
            source = self._fanner.deps.map.find(current.provider.name, current.model)
            # A source's grade or loaded state is known without metering anything, so this check
            # runs first and never touches a seat or the rate limiter for a binding it rejects.
            static_reason = static_spill_reason(source, self._tempo)
            if static_reason is not None and current.fallback is not None:
                await self._spill(current, current.fallback, static_reason)
                current = current.fallback
                continue
            attempt = await self._meter(current, request, source)
            if attempt is None:
                # Queueing for the seat ate too much of the latency budget, and a fallback exists;
                # _meter already released the seat and recorded the spill before returning None.
                current = current.fallback if current.fallback is not None else current
                continue
            return await self._call(attempt, request)

    async def _meter(
        self, current: BoundModel, request: LLMRequest, source: ModelSource | None
    ) -> _Attempt | None:
        """Rate-limit and seat-queue `current`; return None (having spilled) on excess queueing.

        Returns:
            A resolved `_Attempt` ready for `_call`, or None when the seat wait exceeded the
            tempo's budget and a fallback binding exists (the seat has already been released and
            the spill already recorded by the time this returns None).
        """
        seat_meter = await self._fanner.seat_meter_for(current.provider.name)
        rate_limiter = await self._fanner.rate_limiter_for(current.provider.name)
        estimated_tokens = estimate_tokens(request)
        # External wait: throttled by this provider's own manifest rate limit; the Fanner never
        # refuses a call, so there is no timeout here, only a wait (hivemind.llm.fanner.limiter).
        await rate_limiter.acquire(estimated_tokens)
        # External wait: queueing for a seat on a possibly busy provider, bounded below by the
        # queue-wait spill check right after (hivemind.llm.fanner.seats).
        waited_s = await seat_meter.acquire(self._tempo)
        if queue_wait_exceeded(waited_s, self._tempo) and current.fallback is not None:
            await seat_meter.release()
            await self._spill(current, current.fallback, SpillReason.QUEUE_WAIT_EXCEEDED)
            return None
        return _Attempt(current, source, seat_meter, rate_limiter, estimated_tokens)

    async def _call(self, attempt: _Attempt, request: LLMRequest) -> LLMResponse:
        """Make the metered call, always releasing its seat, and record it on success."""
        # The binding, not the caller, knows which model id the provider should run
        # (hivemind.llm.ladders.gate.DirectCallGate's own docstring); stamped on a copy since
        # every LLMRequest is frozen.
        stamped = request.model_copy(update={"model": attempt.bound.model})
        start_s = self._fanner.deps.clock.monotonic()
        try:
            # External await: the actual model call. Its own timeout is the provider adapter's
            # concern (llm/providers/<name>); the Fanner only measures how long it took.
            response = await attempt.bound.provider.complete(stamped)
        finally:
            # Always release, success or error, so a raised exception never leaks a held seat.
            await attempt.seat_meter.release()
        latency_s = self._fanner.deps.clock.monotonic() - start_s
        actual_tokens = response.usage.input_tokens + response.usage.output_tokens
        attempt.rate_limiter.observe_actual_tokens(attempt.estimated_tokens, actual_tokens)
        await self._record_success(attempt, response, latency_s)
        return response

    async def _record_success(
        self, attempt: _Attempt, response: LLMResponse, latency_s: float
    ) -> None:
        """Update the Forage map (when the source is known) and record the llm.call event."""
        if attempt.source is not None:
            tokens_per_s = response.usage.output_tokens / max(latency_s, _LATENCY_EPSILON_S)
            await self._fanner.deps.map.observe(attempt.source.source_id, latency_s, tokens_per_s)
            free_seats = max(attempt.seat_meter.capacity - attempt.seat_meter.in_flight, 0)
            await self._fanner.deps.map.set_abundance(attempt.source.source_id, free_seats)
        await self._fanner.deps.recorder.record(
            kind=LLM_CALL_KIND,
            subject_id=new_event_id(self._fanner.deps.clock),
            payload=_call_payload(attempt.bound, response, latency_s),
        )

    async def _spill(self, current: BoundModel, target: BoundModel, reason: SpillReason) -> None:
        """Record an llm.spill event for moving from `current` to `target` because of `reason`."""
        await self._fanner.deps.recorder.record(
            kind=LLM_SPILL_KIND,
            subject_id=new_event_id(self._fanner.deps.clock),
            payload=_spill_payload(current, target, reason),
        )


def _call_payload(current: BoundModel, response: LLMResponse, latency_s: float) -> JsonObject:
    """Build the llm.call payload: LlmEvent's required fields for that kind, plus latency."""
    usage = response.usage
    return {
        "slot": current.slot.value,
        "provider": current.provider.name,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            # LlmUsage.cost_usd is never None (unlike llm.models.Usage.cost_usd): an unpriced
            # call costs 0 on the trail, mirroring LlmUsage's own docstring ("0 if unpriced").
            "cost_usd": usage.cost_usd if usage.cost_usd is not None else 0.0,
        },
        "latency_s": latency_s,
    }


def _spill_payload(current: BoundModel, target: BoundModel, reason: SpillReason) -> JsonObject:
    """Build the llm.spill payload: ids and the enum reason only, no text."""
    return {
        "slot": current.slot.value,
        "provider": current.provider.name,
        "from_binding": current.binding,
        "to_binding": target.binding,
        "reason": reason.value,
    }
