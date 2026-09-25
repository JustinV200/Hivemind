"""Define FannerTranscriptionGate: the Fanner's metering, for the model slot that hears.

The Fanner (the seat meter every model call in the Hive passes through, codingrules section 8.10)
meters transcription exactly as it meters chat (ADR-0033): every call on `ModelSlot.TRANSCRIBER`
takes a seat from the same per-provider `SeatMeter` chat calls share (`Fanner.seat_meter_for`),
waits on the same per-provider rate limiter (one request, no tokens), is timed on the injected
clock, and is recorded as one `llm.call` Pheromone Trail event (the Hive's append-only audit log)
carrying the slot, provider, model, the clip's audio seconds and the latency -- never the audio
and never the transcript. The Forage map (every source that can serve a model) sees the call too:
the source's latency and free seats are updated, and a rate-limited source is throttled. Spill-
over follows the chat lane's rules (a throttled, under-graded or unloaded source, or a seat wait
past the tempo's budget, moves the call to the next binding), and because no degradation ladder
sits above a transcription, an outage on one binding also moves the call along the chain here.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.fanner`. Implements
    `hivemind.llm.transcription.gate.TranscriptionGate`; built by the composition root over the
    process's one `Fanner` and handed to whoever hears (Buzz's `listen` tool, the Hive Entrance's
    voice route). Calls into this package's `lane`, `spill` and (through the Fanner) `seats`,
    `limiter` and `recorder`, `hivemind.forage`, `hivemind.llm.ladders.observer` (the fallback
    vocabulary) and `hivemind.llm.transcription`.

Key invariants:
    - A seat is always released and `call_finished` always follows `call_started`, success or
      error (`_call`'s `finally`), exactly as in `FannerLane`.
    - Exactly one `llm.call` per transcription that returned; its payload holds ids, numbers and
      the model id only (codingrules section 12: no event carries audio or text), with zero
      tokens, since a transcription spends none.
    - The Fanner never refuses a call: with no fallback left, a spill reason is ignored and the
      call is made; an outage or rate limit on the last binding reaches the caller unchanged.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for "metered by the Fanner".
    - hivemind.llm.fanner.lane for FannerLane, the chat gate whose rules this mirrors.
    - hivemind.llm.transcription.gate for the TranscriptionGate Protocol implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from hivemind.forage.models import ModelSource
from hivemind.forage.tempo import Tempo
from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.llm.fanner.lane import (
    DEFAULT_THROTTLE_S,
    LLM_CALL_KIND,
    LLM_SPILL_KIND,
    LLM_THROTTLED_KIND,
    Fanner,
)
from hivemind.llm.fanner.seats import SeatMeter
from hivemind.llm.fanner.spill import SpillReason, queue_wait_exceeded, static_spill_reason
from hivemind.llm.ladders.observer import FALLBACK_KIND, FallbackReason
from hivemind.llm.models import JsonObject
from hivemind.llm.transcription import AudioClip, BoundTranscriber, Transcript
from waggle.ids import new_event_id

TRANSCRIPTION_TOKENS = 0  # A transcription spends one request and no tokens against rate limits.
# The llm.call payload key carrying a transcription's audio length in seconds, public so a reader
# of the trail (a test, a spend view) never spells it twice.
AUDIO_SECONDS_KEY = "audio_seconds"
# A transcription generates no tokens, so the map's speed figure for it is zero; its distance is
# its latency, which is what routing reads.
TRANSCRIPTION_TOKENS_PER_S = 0.0

__all__ = [
    "AUDIO_SECONDS_KEY",
    "TRANSCRIPTION_TOKENS",
    "TRANSCRIPTION_TOKENS_PER_S",
    "FannerTranscriptionGate",
]


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One binding's metered attempt: the link, its map source and the seat it holds."""

    bound: BoundTranscriber
    source: ModelSource | None
    seat_meter: SeatMeter


class FannerTranscriptionGate:
    """Meter, spill and record every transcription; the Fanner's TranscriptionGate."""

    def __init__(
        self,
        fanner: Fanner,
        tempo: Tempo,
        *,
        grant_id: str | None = None,
        goal_id: str | None = None,
    ) -> None:
        """Bind this gate to the process's Fanner and the caller's tempo.

        Args:
            fanner: The one Fanner whose seat meters, rate limiters, map and recorder chat calls
                share; a transcription draws on the same provider budgets.
            tempo: The caller's speed-against-accuracy setting: its place in every seat queue,
                its grade floor and its spill threshold.
            grant_id: Carried onto every `llm.call` this gate records, for the Forage ledger's
                spend books, exactly as `FannerLane` carries it.
            goal_id: The goal those calls serve, on the same terms.
        """
        self._fanner = fanner
        self._tempo = tempo
        self._grant_id = grant_id
        self._goal_id = goal_id

    async def transcribe(
        self, bound: BoundTranscriber, clip: AudioClip, language: str | None = None
    ) -> Transcript:
        """Transcribe through `bound`'s chain, metering each attempt; see TranscriptionGate.

        Raises:
            ProviderRequestError: A provider refused the request itself; never retried.
            RateLimitedError: The last link was rate-limited (after throttling its source).
            ProviderUnavailableError: The last link could not be reached or run.
        """
        current = bound
        # One pass per binding tried: spill past it, meter and call it, or move past a failure.
        while True:
            source = self._fanner.deps.map.find(current.provider.name, current.model)
            # Grade, loaded state and throttle are known before metering anything, so a source
            # the map already rules out never touches a seat or the rate limiter.
            reason = static_spill_reason(source, self._tempo)
            if reason is not None and current.fallback is not None:
                await self._record_move(LLM_SPILL_KIND, current, current.fallback, reason.value)
                current = current.fallback
                continue
            try:
                outcome = await self._attempt(current, source, clip, language)
            except (ProviderUnavailableError, RateLimitedError) as exc:
                following = await self._next_after(current, source, exc)
                if following is None:
                    raise  # Nowhere left to go: the caller sees the last link's own error.
                current = following
                continue
            if isinstance(outcome, Transcript):
                return outcome
            current = outcome  # The seat wait ran past the budget; _attempt already spilled.

    async def _attempt(
        self,
        current: BoundTranscriber,
        source: ModelSource | None,
        clip: AudioClip,
        language: str | None,
    ) -> Transcript | BoundTranscriber:
        """Rate-limit, take a seat and call; or spill, returning the next link to try."""
        seat_meter = await self._fanner.seat_meter_for(current.provider.name)
        rate_limiter = await self._fanner.rate_limiter_for(current.provider.name)
        # External wait: the provider's manifest rate limit, one request and no tokens; the
        # Fanner never refuses, so this only ever waits (hivemind.llm.fanner.limiter).
        await rate_limiter.acquire(TRANSCRIPTION_TOKENS)
        # External wait: a seat on a possibly busy provider, ordered by tempo, then judged
        # against the tempo's latency budget right below.
        waited_s = await seat_meter.acquire(self._tempo)
        if queue_wait_exceeded(waited_s, self._tempo) and current.fallback is not None:
            await seat_meter.release()
            reason = SpillReason.QUEUE_WAIT_EXCEEDED.value
            await self._record_move(LLM_SPILL_KIND, current, current.fallback, reason)
            return current.fallback
        return await self._call(_Attempt(current, source, seat_meter), clip, language)

    async def _call(self, attempt: _Attempt, clip: AudioClip, language: str | None) -> Transcript:
        """Make the metered call, always releasing its seat, and record it once it returns."""
        deps = self._fanner.deps
        source_id = attempt.source.source_id if attempt.source is not None else None
        provider = attempt.bound.provider.name
        start_s = deps.clock.monotonic()
        # The seat is held from here to the finally below: the window the ledger counts in use.
        await deps.recorder.call_started(source_id, provider)
        try:
            # External await: the provider's own transcription, bounded by its own timeout; the
            # Fanner only measures how long it took.
            transcript = await attempt.bound.provider.transcribe(clip, language)
        finally:
            await attempt.seat_meter.release()
            await deps.recorder.call_finished(source_id, provider)
        latency_s = deps.clock.monotonic() - start_s
        await self._record_call(attempt, clip, latency_s)
        return transcript

    async def _record_call(self, attempt: _Attempt, clip: AudioClip, latency_s: float) -> None:
        """Update the map's figures (when the source is known) and record the one llm.call."""
        deps = self._fanner.deps
        if attempt.source is not None:
            source_id = attempt.source.source_id
            await deps.map.observe(source_id, latency_s, TRANSCRIPTION_TOKENS_PER_S)
            free_seats = max(attempt.seat_meter.capacity - attempt.seat_meter.in_flight, 0)
            await deps.map.set_abundance(source_id, free_seats)
        payload = self._call_payload(attempt.bound, clip, latency_s)
        await deps.recorder.record(
            kind=LLM_CALL_KIND, subject_id=new_event_id(deps.clock), payload=payload
        )

    async def _next_after(
        self,
        current: BoundTranscriber,
        source: ModelSource | None,
        exc: ProviderUnavailableError | RateLimitedError,
    ) -> BoundTranscriber | None:
        """Record what a failed attempt means and return the next link, or None at the end.

        A rate limit throttles the source on the map and records `llm.throttled` (the chat
        lane's rule); an outage records `llm.fallback` when there is somewhere to fall back to.
        """
        if isinstance(exc, RateLimitedError):
            await self._throttle(current, source, exc.retry_after_s)
            return current.fallback
        if current.fallback is not None:
            reason = FallbackReason.PROVIDER_UNAVAILABLE.value
            await self._record_move(FALLBACK_KIND, current, current.fallback, reason)
        return current.fallback

    async def _throttle(
        self, current: BoundTranscriber, source: ModelSource | None, retry_after_s: float | None
    ) -> None:
        """Mask `source`'s headroom until the provider's window passes; record llm.throttled."""
        deps = self._fanner.deps
        wait_s = retry_after_s if retry_after_s is not None else DEFAULT_THROTTLE_S
        if source is not None:
            await deps.map.throttle(source.source_id, deps.clock.now() + timedelta(seconds=wait_s))
        payload: JsonObject = {
            "slot": current.slot.value,
            "provider": current.provider.name,
            "source_id": source.source_id if source is not None else None,
            "wait_s": wait_s,
        }
        await deps.recorder.record(
            kind=LLM_THROTTLED_KIND, subject_id=new_event_id(deps.clock), payload=payload
        )

    async def _record_move(
        self, kind: str, current: BoundTranscriber, target: BoundTranscriber, reason: str
    ) -> None:
        """Record a spill or a fallback from `current` to `target`: binding keys and a reason."""
        payload: JsonObject = {
            "slot": current.slot.value,
            "provider": current.provider.name,
            "from_binding": current.binding,
            "to_binding": target.binding,
            "reason": reason,
        }
        deps = self._fanner.deps
        await deps.recorder.record(kind=kind, subject_id=new_event_id(deps.clock), payload=payload)

    def _call_payload(
        self, bound: BoundTranscriber, clip: AudioClip, latency_s: float
    ) -> JsonObject:
        """Build the llm.call payload: ids and numbers only, zero tokens, the clip's seconds.

        `grant_id`/`goal_id` are omitted, not nulled, when this gate carries neither (the same
        shape `FannerLane` writes, which the Forage ledger reads either way).
        """
        payload: JsonObject = {
            "slot": bound.slot.value,
            "provider": bound.provider.name,
            "model": bound.model,
            # A transcription spends no tokens; an unpriced call costs 0 on the trail (LlmUsage).
            "usage": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0},
            "latency_s": latency_s,
            AUDIO_SECONDS_KEY: clip.duration_s,
        }
        if self._grant_id is not None:
            payload["grant_id"] = self._grant_id
        if self._goal_id is not None:
            payload["goal_id"] = self._goal_id
        return payload
