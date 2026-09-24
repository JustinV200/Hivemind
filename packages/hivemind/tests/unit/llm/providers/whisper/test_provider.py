"""Tests for hivemind.llm.providers.whisper.provider: WhisperLocalTranscription over a stand-in.

Every test hands the adapter a `builders.audio.StandInLoader` at its own seam (`ModelLoader`), so
no real model is loaded or downloaded. The timeout and one-seat tests park a stand-in run on its
worker thread with a `threading.Event` and always release it before finishing, so the event
loop's executor never waits on a thread the test forgot.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/whisper/provider.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.whisper.provider for the module under test.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest
from builders.audio import (
    StandInLoader,
    StandInSegment,
    StandInWhisperModel,
    make_chunks,
    make_tone_clip,
    tone_pcm,
)

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import (
    MalformedOutputError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from hivemind.llm.providers.whisper.config import WhisperConfig
from hivemind.llm.providers.whisper.provider import (
    REFUSED_AUDIO_STATUS_CODE,
    WhisperLocalTranscription,
)
from hivemind.llm.transcription import AudioChunk, TranscriptionCapabilities
from hivemind.llm.transcription.models import MAX_TRANSCRIPT_CHARS
from waggle.clock import FakeClock

_HELD_TIMEOUT_S = 0.05  # A deadline a parked stand-in run is certain to miss.
_ENTRY_WAIT_S = 5.0  # How long a test waits for a worker thread to reach the stand-in.
_SEGMENTS = [
    StandInSegment(start=0.0, end=0.48, text=" Hello from"),
    StandInSegment(start=0.48, end=0.96, text=" the hive."),
]


def _provider(loader: StandInLoader, **overrides: object) -> WhisperLocalTranscription:
    """Build the adapter over `loader`, with any WhisperConfig field overridden."""
    fields: dict[str, object] = {"model": "speech-model"}
    fields.update(overrides)
    return WhisperLocalTranscription("whisper", WhisperConfig(**fields), FakeClock(), loader)


async def _iterate(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` in order."""
    for chunk in chunks:
        yield chunk


async def test_transcribe_loads_once_and_maps_the_models_segments() -> None:
    loader = StandInLoader(StandInWhisperModel(segments=_SEGMENTS))
    provider = _provider(loader)

    first = await provider.transcribe(make_tone_clip(), "en")
    await provider.transcribe(make_tone_clip(), "en")

    assert first.text == "Hello from the hive."
    assert first.language == "en"
    assert loader.loads == 1
    assert [call[1] for call in loader.model.calls] == ["en", "en"]


async def test_transcribe_hands_the_model_the_whole_wav_file() -> None:
    loader = StandInLoader()
    clip = make_tone_clip(0.5)

    await _provider(loader).transcribe(clip)

    assert loader.model.calls == [(len(clip.data), None)]


async def test_transcribe_refuses_a_clip_past_the_ceiling_before_loading() -> None:
    loader = StandInLoader()
    provider = _provider(loader, capabilities=TranscriptionCapabilities(max_clip_s=0.5))

    with pytest.raises(ProviderRequestError):
        await provider.transcribe(make_tone_clip(1.0))
    assert loader.loads == 0


async def test_transcribe_reports_a_missing_extra_as_unavailable_and_health_down() -> None:
    loader = StandInLoader(missing="the extra is absent", failure=ImportError("no module"))
    provider = _provider(loader)

    with pytest.raises(ProviderUnavailableError, match="the extra is absent"):
        await provider.transcribe(make_tone_clip())
    assert (await provider.health()).state is HealthState.DOWN


async def test_a_failed_load_reads_down_until_the_next_call_loads_it() -> None:
    loader = StandInLoader(failure=OSError("weights not found"))
    provider = _provider(loader)

    with pytest.raises(ProviderUnavailableError, match="weights not found"):
        await provider.transcribe(make_tone_clip())
    assert (await provider.health()).state is HealthState.DOWN

    loader.failure = None
    await provider.transcribe(make_tone_clip())

    assert loader.loads == 2
    health = await provider.health()
    assert health.state is HealthState.HEALTHY
    assert health.detail == "model loaded on cpu (int8)"


async def test_health_before_any_load_is_healthy_and_loads_nothing() -> None:
    loader = StandInLoader()

    health = await _provider(loader).health()

    assert health.state is HealthState.HEALTHY
    assert loader.loads == 0


async def test_a_run_the_model_refuses_is_a_request_error() -> None:
    loader = StandInLoader(StandInWhisperModel(failure=ValueError("xx is not a language")))

    with pytest.raises(ProviderRequestError) as caught:
        await _provider(loader).transcribe(make_tone_clip())

    assert caught.value.status_code == REFUSED_AUDIO_STATUS_CODE
    assert caught.value.error_type == "audio_refused"


async def test_runaway_model_output_is_malformed_and_never_quoted() -> None:
    runaway = [StandInSegment(start=0.0, end=1.0, text=" private" * (MAX_TRANSCRIPT_CHARS // 4))]
    loader = StandInLoader(StandInWhisperModel(segments=runaway))

    with pytest.raises(MalformedOutputError) as caught:
        await _provider(loader).transcribe(make_tone_clip())

    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is None


async def test_a_run_the_model_cannot_finish_is_unavailable() -> None:
    loader = StandInLoader(StandInWhisperModel(failure=RuntimeError("device lost")))

    with pytest.raises(ProviderUnavailableError, match="device lost"):
        await _provider(loader).transcribe(make_tone_clip())


async def test_a_load_past_its_deadline_is_unavailable() -> None:
    hold = threading.Event()
    loader = StandInLoader(hold=hold)
    try:
        with pytest.raises(ProviderUnavailableError, match="did not load within"):
            await _provider(loader, load_timeout_s=_HELD_TIMEOUT_S).transcribe(make_tone_clip())
    finally:
        hold.set()


async def test_a_run_past_its_deadline_is_unavailable() -> None:
    hold = threading.Event()
    loader = StandInLoader(StandInWhisperModel(hold=hold))
    try:
        with pytest.raises(ProviderUnavailableError, match="did not finish within"):
            await _provider(loader, timeout_s=_HELD_TIMEOUT_S).transcribe(make_tone_clip())
    finally:
        hold.set()


async def test_one_loaded_model_is_one_seat() -> None:
    hold = threading.Event()
    model = StandInWhisperModel(hold=hold)
    provider = _provider(StandInLoader(model))
    try:
        first = asyncio.ensure_future(provider.transcribe(make_tone_clip()))
        # Wait (on a worker thread, not the loop) until the first run is inside the model.
        assert await asyncio.to_thread(model.entered.wait, _ENTRY_WAIT_S)
        second = asyncio.ensure_future(provider.transcribe(make_tone_clip()))
        await asyncio.sleep(0)  # Let the second call reach, and queue on, the seat.

        assert len(model.calls) == 1
    finally:
        hold.set()
    await asyncio.gather(first, second)

    assert len(model.calls) == 2


async def test_stream_buffers_every_chunk_into_one_run() -> None:
    loader = StandInLoader(StandInWhisperModel(segments=_SEGMENTS))

    segments = [
        segment
        async for segment in _provider(loader).stream(_iterate(make_chunks(tone_pcm())), "en")
    ]

    assert [segment.text for segment in segments] == ["Hello from", "the hive."]
    assert len(loader.model.calls) == 1
