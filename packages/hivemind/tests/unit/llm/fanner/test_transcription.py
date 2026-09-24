"""Tests for hivemind.llm.fanner.transcription: MeteredTranscriber and bind_transcriber.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/transcription.py (codingrules section 3). Every recorder here
    is the real `TrailLlmEventRecorder` over a `MemoryPheromoneTrail`, so an `llm.call` that would
    not validate as an `LlmEvent` (a forbidden key, a missing field) fails the test.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.transcription for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from builders.audio import make_clip, silent_wav
from builders.forage import make_source
from builders.llm import make_binding, make_provider_config, make_registry_deps

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelSource
from hivemind.forage.slots import ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.llm.fanner import AUDIO_SECONDS_KEY, MeteredTranscriber, bind_transcriber
from hivemind.llm.fanner.lane import Fanner, FannerDeps
from hivemind.llm.fanner.recorder import TrailLlmEventRecorder
from hivemind.llm.registry import ProviderRegistry
from hivemind.llm.transcription import (
    AudioChunk,
    AudioClip,
    AudioMediaType,
    BoundTranscriber,
    FakeTranscription,
    Transcript,
    TranscriptionUnsupportedError,
)
from hivemind.pheromone import LlmEvent, MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_WORDS = "please water the tomatoes"  # What every scripted transcript says; never on the trail.
_TEMPO = Tempo(accuracy=AccuracyBar.NORMAL)


def _build_fanner(*sources: ModelSource) -> tuple[Fanner, MemoryPheromoneTrail, FakeClock]:
    """Wire a Fanner over a map of `sources` and a trail-backed recorder, sharing one clock."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailLlmEventRecorder(trail, new_hive_id(clock), new_node_id(clock), "human", clock)
    deps = FannerDeps(
        map=ForageMap(sources, clock=clock), seats={}, limits={}, clock=clock, recorder=recorder
    )
    return Fanner(deps), trail, clock


def _bound(
    provider: FakeTranscription,
    *,
    binding: str = "transcriber",
    price: float | None = None,
    fallback: BoundTranscriber | None = None,
) -> BoundTranscriber:
    """Bind `provider` to TRANSCRIBER on the neutral test model."""
    return BoundTranscriber(
        slot=ModelSlot.TRANSCRIBER,
        binding=binding,
        provider=provider,
        model="test-model",
        cost_per_audio_minute_usd=price,
        fallback=fallback,
    )


class _BlockingTranscription(FakeTranscription):
    """A FakeTranscription whose calls wait on `released`, so a test can hold a seat open."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.released = asyncio.Event()

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Wait until released, then answer from the script."""
        await self.released.wait()
        return await super().transcribe(clip, language)


async def _events(trail: MemoryPheromoneTrail) -> list[LlmEvent]:
    """Return every LlmEvent on the trail, oldest first."""
    return [event for event in await trail.query(TrailQuery()) if isinstance(event, LlmEvent)]


# ──────────────────────────────────────────────────────────────────────────────
# One metered call: exactly one llm.call, and nothing the human said
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_transcription_records_exactly_one_llm_call_on_the_transcriber_slot() -> None:
    fanner, trail, _ = _build_fanner()
    provider = FakeTranscription(name="ears")
    provider.script(_WORDS)
    clip = make_clip(2.0)

    transcript = await MeteredTranscriber(fanner, _bound(provider), _TEMPO).transcribe(clip)

    (event,) = await _events(trail)
    assert transcript.text == _WORDS
    assert (event.kind, event.slot, event.provider) == ("llm.call", "TRANSCRIBER", "ears")
    assert event.usage is not None
    assert (event.usage.input_tokens, event.usage.output_tokens) == (0, 0)
    assert event.usage.cost_usd == 0.0
    assert event.payload[AUDIO_SECONDS_KEY] == pytest.approx(2.0)
    assert isinstance(event.payload["latency_s"], float)


async def test_the_trail_never_carries_the_audio_or_the_transcript() -> None:
    fanner, trail, _ = _build_fanner()
    provider = FakeTranscription()
    provider.script(_WORDS)
    clip = make_clip()

    await MeteredTranscriber(fanner, _bound(provider), _TEMPO).transcribe(clip)

    recorded = "".join(event.model_dump_json() for event in await trail.query(TrailQuery()))
    assert _WORDS not in recorded
    assert "tomatoes" not in recorded
    assert clip.data.hex() not in recorded


async def test_a_priced_binding_records_the_cost_of_its_audio_minutes() -> None:
    fanner, trail, _ = _build_fanner()
    provider = FakeTranscription()
    provider.script(_WORDS)

    await MeteredTranscriber(fanner, _bound(provider, price=0.006), _TEMPO).transcribe(
        make_clip(30.0)
    )

    (event,) = await _events(trail)
    assert event.usage is not None
    assert event.usage.cost_usd == pytest.approx(0.003)


async def test_a_stream_is_metered_as_one_call() -> None:
    fanner, trail, _ = _build_fanner()
    provider = FakeTranscription()
    provider.script(_WORDS)
    wav = silent_wav(1.0)

    async def frames() -> AsyncIterator[AudioChunk]:
        yield AudioChunk(data=wav[:500], media_type=AudioMediaType.WAV)
        yield AudioChunk(data=wav[500:], media_type=AudioMediaType.WAV)

    await MeteredTranscriber(fanner, _bound(provider), _TEMPO).stream(frames())

    assert [event.kind for event in await _events(trail)] == ["llm.call"]


# ──────────────────────────────────────────────────────────────────────────────
# Seats: held for the call, always released
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_failed_call_releases_its_seat_and_records_nothing() -> None:
    fanner, trail, _ = _build_fanner()
    provider = FakeTranscription(name="ears")
    provider.script(ProviderUnavailableError("ears", "HTTP 503"))

    with pytest.raises(ProviderUnavailableError):
        await MeteredTranscriber(fanner, _bound(provider), _TEMPO).transcribe(make_clip())

    assert fanner.in_flight("ears") == 0
    assert await _events(trail) == []


async def test_a_known_source_has_its_free_seats_written_back_after_the_call() -> None:
    source = make_source(source_id="src_ears", provider="ears", model="test-model", seats=2)
    fanner, _, _ = _build_fanner(source)
    provider = FakeTranscription(name="ears")
    provider.script(_WORDS)

    await MeteredTranscriber(fanner, _bound(provider), _TEMPO).transcribe(make_clip())

    assert fanner.deps.map.get("src_ears").abundance.seats_free == 1  # DEFAULT_SEATS, now free.


# ──────────────────────────────────────────────────────────────────────────────
# Spill-over and throttling, as for any model call
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_throttled_head_spills_to_the_fallback_without_calling_the_head() -> None:
    head_source = make_source(source_id="src_head", provider="head", model="test-model")
    fanner, trail, clock = _build_fanner(head_source)
    await fanner.deps.map.throttle("src_head", clock.now() + timedelta(seconds=30))
    head, tail = FakeTranscription(name="head"), FakeTranscription(name="tail")
    tail.script(_WORDS)
    bound = _bound(head, fallback=_bound(tail, binding="hosted_ears"))

    transcript = await MeteredTranscriber(fanner, bound, _TEMPO).transcribe(make_clip())

    kinds = [(event.kind, event.provider) for event in await _events(trail)]
    assert transcript.text == _WORDS
    assert head.calls == []
    assert kinds == [("llm.spill", "head"), ("llm.call", "tail")]


async def test_a_call_that_queued_past_its_latency_budget_spills_to_the_fallback() -> None:
    fanner, trail, clock = _build_fanner()  # One seat per provider: DEFAULT_SEATS.
    head, tail = _BlockingTranscription("head"), FakeTranscription(name="tail")
    head.script(_WORDS)
    tail.script("from the fallback")
    # A tight budget: any real queueing exceeds SPILL_WAIT_FRACTION of it.
    metered = MeteredTranscriber(
        fanner,
        _bound(head, fallback=_bound(tail, binding="hosted_ears")),
        Tempo(latency_budget_s=0.001),
    )

    blocker = asyncio.ensure_future(metered.transcribe(make_clip()))
    await asyncio.sleep(0)  # Takes head's only seat and blocks inside the call.
    waiting = asyncio.ensure_future(metered.transcribe(make_clip()))
    await asyncio.sleep(0)  # Queues behind it on the seat meter.
    clock.advance(1.0)  # The queued call's measured wait now exceeds its budget.
    head.released.set()
    spilled, first = await waiting, await blocker

    spills = [event for event in await _events(trail) if event.kind == "llm.spill"]
    assert (first.text, spilled.text) == (_WORDS, "from the fallback")
    assert [event.payload["reason"] for event in spills] == ["QUEUE_WAIT_EXCEEDED"]
    assert fanner.in_flight("head") == 0


async def test_a_rate_limited_head_is_throttled_and_the_call_spills_to_the_fallback() -> None:
    head_source = make_source(source_id="src_head", provider="head", model="test-model")
    fanner, trail, _ = _build_fanner(head_source)
    head, tail = FakeTranscription(name="head"), FakeTranscription(name="tail")
    head.script(RateLimitedError("head", retry_after_s=12.0))
    tail.script(_WORDS)
    bound = _bound(head, fallback=_bound(tail, binding="hosted_ears"))

    await MeteredTranscriber(fanner, bound, _TEMPO).transcribe(make_clip())

    events = await _events(trail)
    assert [event.kind for event in events] == ["llm.throttled", "llm.call"]
    assert events[0].payload["wait_s"] == 12.0
    assert fanner.deps.map.get("src_head").abundance.throttled_until is not None


async def test_a_rate_limit_with_no_fallback_is_raised_after_being_recorded() -> None:
    fanner, trail, _ = _build_fanner()
    provider = FakeTranscription()
    provider.script(RateLimitedError("fake", retry_after_s=1.0))

    with pytest.raises(RateLimitedError):
        await MeteredTranscriber(fanner, _bound(provider), _TEMPO).transcribe(make_clip())

    assert [event.kind for event in await _events(trail)] == ["llm.throttled"]


# ──────────────────────────────────────────────────────────────────────────────
# The provider face, and bind_transcriber at the composition root
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_metered_transcriber_speaks_for_its_head_provider_and_closes_nothing() -> None:
    fanner, _, _ = _build_fanner()
    provider = FakeTranscription(name="ears")
    metered = MeteredTranscriber(fanner, _bound(provider), _TEMPO)

    health = await metered.health()
    await metered.aclose()

    assert (metered.name, metered.capabilities) == ("ears", provider.capabilities)
    assert health.state is HealthState.HEALTHY
    assert not provider.is_closed  # The registry owns the provider and closes it.


def _registry(kind: str) -> ProviderRegistry:
    """Build a registry whose TRANSCRIBER row binds provider "ears" of `kind`."""
    return ProviderRegistry(
        {"ears": make_provider_config(kind=kind)},
        [make_binding(key="transcriber", provider="ears")],
        offline=False,
        deps=make_registry_deps(),
    )


async def test_bind_transcriber_meters_the_registrys_transcriber_chain() -> None:
    fanner, trail, _ = _build_fanner()
    registry = _registry("fake")

    metered = bind_transcriber(registry, fanner, _TEMPO)
    head = metered.bound.provider
    assert isinstance(head, FakeTranscription)
    head.script(_WORDS)
    await metered.transcribe(make_clip())

    assert metered.bound.binding == "transcriber"
    assert [event.slot for event in await _events(trail)] == ["TRANSCRIBER"]


def test_bind_transcriber_refuses_a_kind_that_cannot_transcribe_before_any_call() -> None:
    fanner, _, _ = _build_fanner()

    with pytest.raises(TranscriptionUnsupportedError):
        bind_transcriber(_registry("anthropic"), fanner, _TEMPO)
