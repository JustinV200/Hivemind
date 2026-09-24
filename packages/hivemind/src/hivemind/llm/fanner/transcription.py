"""Define MeteredTranscriber: every transcription passes through the Fanner like any model call.

The Fanner (the seat meter every model call passes through, codingrules section 8.10) owns each
provider's seats and rate limit; a transcription is a model call like any other, so it takes a
seat on its provider, waits its turn under the provider's rate limit, spills to the next link of
its `BoundTranscriber` chain for the same reasons a chat call does (the source is throttled,
below the tempo's grade floor or not loaded, or the seat queue ate too much of the latency
budget, or the provider just rate-limited it), and records exactly one `llm.call` event on the
TRANSCRIBER slot when it lands: slot, provider, latency, the seconds of audio (`audio_s`) and the
cost when the binding is priced. Neither the audio nor the transcript ever reaches the trail.
`MeteredTranscriber` is itself a `TranscriptionProvider`, so the Entrance's voice route depends
only on the protocol (its tests hand it a `FakeTranscription`), and `bind_transcriber` is the one
call a composition root makes to get one.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.fanner`. Built by a composition root
    (the Entrance's, roadmap step 10.5f) through `bind_transcriber`; shares the one `Fanner`'s
    seat meters, rate limiters, Forage map and event recorder with every chat call in the
    process. Calls into `hivemind.llm.transcription`, `hivemind.llm.registry` (to bind the slot)
    and this package's `lane`, `seats` and `spill` modules.

Key invariants:
    - Seats and rate limits are per provider name, shared with that provider's chat calls: one
      server's concurrency is one budget, whatever it is asked to do (as in `lane`).
    - A held seat is always released and `call_started` always matched by `call_finished`,
      success or error.
    - The `llm.call` payload carries ids, numbers and enum values only; `usage` reports zero
      tokens (audio is not tokens) and the cost, with the audio's seconds beside it as `audio_s`,
      because the trail's `LlmUsage` has no audio field of its own.
    - A transcriber source's Forage-map distance is not updated: `Distance.tokens_per_s` has no
      meaning for audio, and inventing one would corrupt routing. Free seats are updated.
    - `aclose()` closes nothing: the registry built the providers and closes them.

See Also:
    - .claude/codingrules.md section 8.10 for the Fanner's role and spill-over.
    - hivemind.llm.fanner.lane for FannerLane, the chat counterpart this follows.
    - hivemind.llm.transcription for the protocol, the models and BoundTranscriber.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import timedelta

from hivemind.forage.models import ModelSource
from hivemind.forage.tempo import Tempo
from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.errors import RateLimitedError
from hivemind.llm.fanner.lane import (
    DEFAULT_THROTTLE_S,
    LLM_CALL_KIND,
    LLM_SPILL_KIND,
    LLM_THROTTLED_KIND,
    Fanner,
)
from hivemind.llm.fanner.seats import SeatMeter
from hivemind.llm.fanner.spill import SpillReason, queue_wait_exceeded, static_spill_reason
from hivemind.llm.models import JsonObject
from hivemind.llm.registry import ProviderRegistry
from hivemind.llm.transcription import (
    AudioChunk,
    AudioClip,
    BoundTranscriber,
    Transcript,
    TranscriptionCapabilities,
    clip_from_chunks,
)
from waggle.ids import new_event_id

AUDIO_SECONDS_KEY = "audio_s"  # The llm.call payload key carrying a transcription's audio length.
_NO_TOKENS = 0  # Audio is not tokens: nothing to reserve from, or report to, a token budget.

__all__ = ["AUDIO_SECONDS_KEY", "MeteredTranscriber", "bind_transcriber"]


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One link's metered attempt: the link, its map source and the seat it holds."""

    bound: BoundTranscriber
    source: ModelSource | None
    seat_meter: SeatMeter


class MeteredTranscriber:
    """A TranscriptionProvider whose every call is metered by the Fanner on one bound chain."""

    def __init__(self, fanner: Fanner, bound: BoundTranscriber, tempo: Tempo) -> None:
        """Meter `bound`'s chain through `fanner`, queueing and spilling by `tempo`.

        Args:
            fanner: The process's one Fanner, shared with every chat call.
            bound: The TRANSCRIBER chain (`ProviderRegistry.transcriber()`).
            tempo: How urgent these calls are: orders their seat queue and sets their spill
                threshold. A human waiting at the door is the Entrance's choice to make.
        """
        self._fanner = fanner
        self._bound = bound
        self._tempo = tempo

    @property
    def name(self) -> str:
        """Return the head provider's manifest name; see `TranscriptionProvider.name`."""
        return self._bound.provider.name

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return the head provider's capabilities, the ones a caller should plan around."""
        return self._bound.provider.capabilities

    @property
    def bound(self) -> BoundTranscriber:
        """Return the chain this transcriber meters, head first."""
        return self._bound

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Transcribe `clip` on the first link that takes it; see `TranscriptionProvider`.

        Raises:
            Whatever the last link tried raises, unchanged: the Fanner meters and spills, it
            never retries, never converts an error and never refuses a call.
        """
        current = self._bound
        while True:
            source = self._fanner.deps.map.find(current.provider.name, current.model)
            reason = static_spill_reason(source, self._tempo)
            # A link the map already rules out is skipped before it takes a seat, while
            # another link remains to take the call.
            if reason is not None and current.fallback is not None:
                current = await self._spill(current, current.fallback, reason)
                continue
            step = await self._take_seat(current, source)
            if isinstance(step, BoundTranscriber):
                current = step  # Queueing ate the budget: seat released, spill recorded.
                continue
            try:
                return await self._call(step, clip, language)
            except RateLimitedError as exc:
                # Mask the source for as long as the provider asked, then spill if possible.
                await self._throttle(current, source, exc.retry_after_s)
                if current.fallback is None:
                    raise
                current = current.fallback

    async def stream(
        self, chunks: AsyncIterable[AudioChunk], language: str | None = None
    ) -> Transcript:
        """Gather the frames into one clip and transcribe it, metered as one call."""
        clip = await clip_from_chunks(chunks)
        return await self.transcribe(clip, language)

    async def health(self) -> ProviderHealth:
        """Return the head provider's health; see `TranscriptionProvider.health`."""
        return await self._bound.provider.health()

    async def aclose(self) -> None:
        """Close nothing: the registry that built the providers owns and closes them."""
        return None

    async def _take_seat(
        self, current: BoundTranscriber, source: ModelSource | None
    ) -> _Attempt | BoundTranscriber:
        """Rate-limit and seat-queue `current`.

        Returns:
            The attempt holding its seat; or, when the queue ate too much of the tempo's latency
            budget and a fallback exists, that fallback (the seat released, the spill recorded).
        """
        limiter = await self._fanner.rate_limiter_for(current.provider.name)
        # External wait under the provider's own request-rate limit; the Fanner never refuses,
        # so there is no timeout on it, only the wait itself.
        await limiter.acquire(_NO_TOKENS)
        seat_meter = await self._fanner.seat_meter_for(current.provider.name)
        # External wait for a seat on a possibly busy provider, ordered by tempo.
        waited_s = await seat_meter.acquire(self._tempo)
        if current.fallback is not None and queue_wait_exceeded(waited_s, self._tempo):
            await seat_meter.release()
            return await self._spill(current, current.fallback, SpillReason.QUEUE_WAIT_EXCEEDED)
        return _Attempt(current, source, seat_meter)

    async def _call(self, attempt: _Attempt, clip: AudioClip, language: str | None) -> Transcript:
        """Make the metered call, always releasing its seat, and record it once it lands."""
        deps = self._fanner.deps
        provider = attempt.bound.provider.name
        source_id = attempt.source.source_id if attempt.source is not None else None
        start_s = deps.clock.monotonic()
        await deps.recorder.call_started(source_id, provider)
        try:
            # External await, seconds to minutes: the adapter bounds it with its own timeout.
            transcript = await attempt.bound.provider.transcribe(clip, language)
        finally:
            # Released on success and error alike, so a failed call never leaks a held seat.
            await attempt.seat_meter.release()
            await deps.recorder.call_finished(source_id, provider)
        latency_s = deps.clock.monotonic() - start_s
        if attempt.source is not None:
            free_seats = max(attempt.seat_meter.capacity - attempt.seat_meter.in_flight, 0)
            await deps.map.set_abundance(attempt.source.source_id, free_seats)
        await deps.recorder.record(
            kind=LLM_CALL_KIND,
            subject_id=new_event_id(deps.clock),
            payload=_call_payload(attempt.bound, transcript, latency_s),
        )
        return transcript

    async def _spill(
        self, current: BoundTranscriber, target: BoundTranscriber, reason: SpillReason
    ) -> BoundTranscriber:
        """Record an llm.spill from `current` to `target`; return `target` to move on to."""
        payload: JsonObject = {
            "slot": current.slot.value,
            "provider": current.provider.name,
            "from_binding": current.binding,
            "to_binding": target.binding,
            "reason": reason.value,
        }
        clock = self._fanner.deps.clock
        await self._fanner.deps.recorder.record(
            kind=LLM_SPILL_KIND, subject_id=new_event_id(clock), payload=payload
        )
        return target

    async def _throttle(
        self, current: BoundTranscriber, source: ModelSource | None, retry_after_s: float | None
    ) -> None:
        """Mask `source` until the provider's wait is over and record llm.throttled."""
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


def bind_transcriber(
    registry: ProviderRegistry, fanner: Fanner, tempo: Tempo
) -> MeteredTranscriber:
    """Bind the TRANSCRIBER slot and meter it: the one call a composition root makes for voice.

    Args:
        registry: The Hive's provider registry; its `transcriber()` builds the chain.
        fanner: The process's one Fanner.
        tempo: How urgent transcriptions are for this caller.

    Returns:
        A TranscriptionProvider whose every call is metered and recorded.

    Raises:
        TranscriptionUnsupportedError: The manifest binds TRANSCRIBER (or a fallback) to a
            provider kind that cannot transcribe; raised here, when the caller starts, never on
            the first spoken word.
        UnknownProviderError: A row names no configured provider.
        OfflineViolationError: The Hive is offline and a row's provider is not local.

    Example:
        At the composition root, once::

            transcriber = bind_transcriber(registry, fanner, Tempo(latency_budget_s=10.0))

        and in the voice route, per clip::

            clip = AudioClip.from_upload(data, "audio/webm;codecs=opus", duration_s=4.2)
            transcript = await transcriber.transcribe(clip, language="en-US")
    """
    return MeteredTranscriber(fanner, registry.transcriber(), tempo)


def _call_payload(bound: BoundTranscriber, transcript: Transcript, latency_s: float) -> JsonObject:
    """Build the llm.call payload: ids and numbers, never a word of the transcript."""
    cost = bound.cost_usd(transcript.duration_s)
    return {
        "slot": bound.slot.value,
        "provider": bound.provider.name,
        "usage": {
            "input_tokens": _NO_TOKENS,
            "output_tokens": _NO_TOKENS,
            "cached_tokens": _NO_TOKENS,
            # LlmUsage.cost_usd is never None: an unpriced call costs 0 on the trail.
            "cost_usd": cost if cost is not None else 0.0,
        },
        "latency_s": latency_s,
        AUDIO_SECONDS_KEY: transcript.duration_s,
    }
