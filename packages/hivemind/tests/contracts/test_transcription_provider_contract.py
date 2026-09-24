"""Contract suite for TranscriptionProvider: one clause per test, run over every implementation.

Fits into the Hive:
    Test infrastructure, not shipped. Each test states one clause of the
    `hivemind.llm.transcription.TranscriptionProvider` contract (codingrules 8.6: "same contract
    for every provider"; 14.3: every TranscriptionProvider passes its suite against recorded HTTP
    exchanges or fixture clips) and runs against every implementation registered in `_HARNESSES`:
    `FakeTranscription`, `OpenAICompatTranscription` (recorded replies over
    `httpx.MockTransport`) and `WhisperLocalTranscription` (a stand-in model at its loader seam).
    The fixture clips are generated (`builders.audio`), never recordings of anyone. The one
    `local_llm` test at the bottom runs a real Whisper model, only when one is already on disk.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the contract these clauses prove.
    - contracts.transcription_provider_harness for every harness.
    - contracts.test_llm_provider_contract for the chat suite this mirrors.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from builders.audio import make_chunks, make_silence_clip, make_tone_clip, tone_pcm
from contracts.transcription_provider_harness import (
    CANONICAL_SEGMENTS,
    CANONICAL_TEXT,
    RETRY_AFTER_S,
    FakeHarness,
    OpenAICompatHarness,
    TranscriptionHarness,
    WhisperHarness,
)

from hivemind.forage.map import ForageMap
from hivemind.forage.slots import ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm import (
    AudioChunk,
    BoundTranscriber,
    DirectTranscriptionGate,
    Fanner,
    FannerDeps,
    FannerTranscriptionGate,
    HealthState,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
    TrailLlmEventRecorder,
    TranscriptionCapabilities,
    TranscriptionProvider,
)
from hivemind.llm.providers.whisper import WhisperConfig, WhisperLocalTranscription
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock, SystemClock
from waggle.ids import new_hive_id, new_node_id

_HARNESSES: dict[str, TranscriptionHarness] = {
    "fake": FakeHarness(),
    "openai_compat": OpenAICompatHarness(),
    "whisper_local": WhisperHarness(),
}


@pytest.fixture(params=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> TranscriptionHarness:
    """One TranscriptionHarness per registered TranscriptionProvider implementation."""
    return _HARNESSES[request.param]


async def _iterate(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` in order, as a push-to-talk stream delivers them."""
    for chunk in chunks:
        yield chunk


def _bound(provider: TranscriptionProvider) -> BoundTranscriber:
    """Bind `provider` as the whole TRANSCRIBER chain."""
    return BoundTranscriber(
        slot=ModelSlot.TRANSCRIBER, binding="transcriber", provider=provider, model="speech-model"
    )


# ──────────────────────────────────────────────────────────────────────────────
# transcribe()
# ──────────────────────────────────────────────────────────────────────────────


async def test_transcribe_returns_the_speech_as_trimmed_time_ordered_segments(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_speech()

    transcript = await provider.transcribe(make_tone_clip(1.0))

    assert transcript.text == CANONICAL_TEXT
    expected = [(text, start, end) for text, start, end, _ in CANONICAL_SEGMENTS]
    assert [(s.text, s.start_s, s.end_s) for s in transcript.segments] == expected


async def test_transcribe_reports_the_clips_own_duration(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_speech()
    clip = make_tone_clip(1.0)

    assert (await provider.transcribe(clip)).duration_s == clip.duration_s


async def test_every_confidence_is_a_probability_or_absent(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_speech()

    transcript = await provider.transcribe(make_tone_clip())

    assert all(s.confidence is None or 0 <= s.confidence <= 1 for s in transcript.segments)


async def test_transcribe_passes_the_language_hint_down_and_reports_it(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_speech()

    transcript = await provider.transcribe(make_tone_clip(), "en")

    assert transcript.language == "en"
    assert harness.heard() == ["en"]


async def test_transcribe_of_silence_is_an_empty_transcript(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_silence()

    transcript = await provider.transcribe(make_silence_clip(1.0))

    assert transcript.text == ""
    assert transcript.segments == ()


async def test_a_clip_past_the_declared_ceiling_is_refused_before_any_work(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider(TranscriptionCapabilities(max_clip_s=0.5))

    with pytest.raises(ProviderRequestError):
        await provider.transcribe(make_tone_clip(1.0))
    assert harness.heard() == []


async def test_an_undeclared_language_is_refused_before_any_work(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider(TranscriptionCapabilities(languages=("en",)))

    with pytest.raises(ProviderRequestError):
        await provider.transcribe(make_tone_clip(), "fr")
    assert harness.heard() == []


async def test_an_outage_is_a_typed_unavailable_error(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_error("unavailable")

    with pytest.raises(ProviderUnavailableError):
        await provider.transcribe(make_tone_clip())


async def test_a_rate_limit_is_a_typed_error_carrying_its_hint(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    if not harness.arrange_error("rate_limited"):
        pytest.skip(f"{type(harness).__name__} has no rate limit to hit.")

    with pytest.raises(RateLimitedError) as caught:
        await provider.transcribe(make_tone_clip())

    assert caught.value.retry_after_s == RETRY_AFTER_S


# ──────────────────────────────────────────────────────────────────────────────
# stream(), health(), capabilities, and the gates in front of every provider
# ──────────────────────────────────────────────────────────────────────────────


async def test_stream_yields_what_transcribe_would_from_one_transcription(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_speech()

    segments = [s async for s in provider.stream(_iterate(make_chunks(tone_pcm(), 5)), "en")]

    assert [s.text for s in segments] == [text for text, *_ in CANONICAL_SEGMENTS]
    assert harness.heard() == ["en"]


async def test_stream_of_nothing_yields_nothing_and_calls_nothing(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()

    assert [s async for s in provider.stream(_iterate([]))] == []
    assert harness.heard() == []


async def test_health_is_healthy_then_down_as_arranged(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()
    assert (await provider.health()).state is HealthState.HEALTHY

    harness.arrange_down()

    assert (await provider.health()).state is HealthState.DOWN


def test_capabilities_are_declared_and_the_name_is_the_manifest_key(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider(TranscriptionCapabilities(max_clip_s=42.0))

    assert provider.capabilities.max_clip_s == 42.0
    assert provider.name in _HARNESSES


async def test_the_direct_gate_serves_every_provider(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_speech()

    transcript = await DirectTranscriptionGate().transcribe(_bound(provider), make_tone_clip())

    assert transcript.text == CANONICAL_TEXT


async def test_the_fanner_records_one_call_per_transcription_and_never_its_text(
    harness: TranscriptionHarness,
) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailLlmEventRecorder(trail, new_hive_id(clock), new_node_id(clock), "system", clock)
    deps = FannerDeps(map=ForageMap([], clock), seats={}, limits={}, clock=clock, recorder=recorder)
    gate = FannerTranscriptionGate(Fanner(deps), Tempo(accuracy=AccuracyBar.NORMAL))
    provider = harness.make_provider()
    harness.arrange_speech()

    await gate.transcribe(_bound(provider), make_tone_clip(1.0))

    events = await trail.query(TrailQuery())
    assert [event.kind for event in events] == ["llm.call"]
    assert events[0].payload["audio_seconds"] == 1.0
    assert CANONICAL_TEXT not in events[0].model_dump_json()


# ──────────────────────────────────────────────────────────────────────────────
# local_llm: a real Whisper model, only when one is already on disk
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.local_llm
async def test_whisper_local_transcribes_a_generated_clip_on_a_real_model() -> None:
    # HIVEMIND_LOCAL_WHISPER_MODEL names a model already on disk (a size name already cached, or
    # a converted model directory); local_files_only means a missing one is skipped, never fetched.
    model = os.environ.get("HIVEMIND_LOCAL_WHISPER_MODEL")
    if not model:
        pytest.skip("HIVEMIND_LOCAL_WHISPER_MODEL names no local Whisper model.")
    provider = WhisperLocalTranscription(
        "whisper", WhisperConfig(model=model, local_files_only=True), SystemClock()
    )
    clip = make_tone_clip(2.0)

    try:
        transcript = await provider.transcribe(clip, "en")
    except ProviderUnavailableError as exc:
        pytest.skip(f"no usable local model: {exc}")

    assert transcript.duration_s == clip.duration_s
    assert transcript.language == "en"
    starts = [segment.start_s for segment in transcript.segments]
    assert starts == sorted(starts)
    assert (await provider.health()).state is HealthState.HEALTHY
