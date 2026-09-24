"""Contract suite for TranscriptionProvider: one clause per test, run over every implementation.

Fits into the Hive:
    Test infrastructure (codingrules 14.3: "every TranscriptionProvider implementation passes
    its provider contract suite against recorded HTTP cassettes or fixture clips"). Each test
    states one clause of `hivemind.llm.transcription.TranscriptionProvider` and runs against
    every harness in `_HARNESSES`: `FakeTranscription` and `OpenAICompatTranscription` (over
    `httpx.MockTransport`, answering recorded `verbose_json` and plain `json` bodies). Clips are
    silent WAVs generated per test (`builders.audio`). Clauses run at both capability levels,
    `full()` and `none()`, so the weakest provider is proven too. A new transcription provider
    passes this suite before it is registered.

Key invariants:
    - None: this module holds tests only.

See Also:
    - contracts.transcription_provider_harness for the harnesses.
    - contracts.test_llm_provider_contract for the chat counterpart.
    - hivemind.llm.transcription.provider for the protocol under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.audio import make_clip, silent_wav
from contracts.transcription_provider_harness import (
    DETECTED_LANGUAGE,
    RETRY_AFTER_S,
    SPOKEN_DURATION_S,
    SPOKEN_SEGMENTS,
    SPOKEN_TEXT,
    ErrorKind,
    FakeHarness,
    OpenAICompatHarness,
    TranscriptionHarness,
)

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import (
    LLMError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from hivemind.llm.transcription import (
    UNSUPPORTED_MEDIA_TYPE_STATUS,
    AudioChunk,
    AudioClip,
    AudioMediaType,
    InvalidAudioClipError,
    TranscriptionCapabilities,
)

_SECRET = "supersecret-value"  # noqa: S105 -- a probe value, never a real credential.
_OPUS_BYTES = b"OggS" + b"\x00" * 60  # Opaque compressed bytes: never decoded.

_HARNESSES: dict[str, TranscriptionHarness] = {
    "fake": FakeHarness(),
    "openai_compat": OpenAICompatHarness(),
}


@pytest.fixture(params=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> TranscriptionHarness:
    """One TranscriptionHarness per registered TranscriptionProvider implementation."""
    return _HARNESSES[request.param]


async def _frames(*chunks: AudioChunk) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` as a push-to-talk stream would."""
    for chunk in chunks:
        yield chunk


# ──────────────────────────────────────────────────────────────────────────────
# 1. What comes back: text, timed segments, duration, language
# ──────────────────────────────────────────────────────────────────────────────


async def test_transcribe_returns_the_words_with_their_timed_segments(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_answer(verbose=True)

    transcript = await provider.transcribe(make_clip(SPOKEN_DURATION_S))

    assert transcript.text == SPOKEN_TEXT
    assert [(s.start_s, s.end_s, s.text) for s in transcript.segments] == list(SPOKEN_SEGMENTS)
    assert transcript.duration_s == pytest.approx(SPOKEN_DURATION_S)


async def test_a_text_only_answer_still_yields_the_words_and_the_clips_duration(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_answer(verbose=False)
    clip = make_clip(SPOKEN_DURATION_S)

    transcript = await provider.transcribe(clip)

    assert transcript.text == SPOKEN_TEXT
    assert transcript.segments == ()
    assert transcript.duration_s == pytest.approx(clip.duration_s)


async def test_a_detecting_provider_names_the_language_it_heard(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_answer(verbose=True)

    transcript = await provider.transcribe(make_clip())

    assert transcript.language == DETECTED_LANGUAGE


async def test_a_language_hint_is_echoed_back_as_its_primary_subtag(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_answer(verbose=True)

    transcript = await provider.transcribe(make_clip(), "en-GB")

    assert transcript.language == "en"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Capability honesty: none() is still a working transcriber
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_provider_declaring_no_segments_or_detection_returns_neither(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider(TranscriptionCapabilities.none())
    harness.arrange_answer(verbose=True)

    transcript = await provider.transcribe(make_clip())

    assert transcript.text == SPOKEN_TEXT
    assert transcript.segments == ()
    assert transcript.language is None


async def test_a_format_the_provider_does_not_decode_is_refused_before_any_upload(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider(TranscriptionCapabilities.none())
    clip = AudioClip.from_upload(_OPUS_BYTES, AudioMediaType.OGG_OPUS, duration_s=1.0)

    with pytest.raises(ProviderRequestError) as excinfo:
        await provider.transcribe(clip)

    assert excinfo.value.status_code == UNSUPPORTED_MEDIA_TYPE_STATUS
    assert harness.uploads() == 0


# ──────────────────────────────────────────────────────────────────────────────
# 3. Failures are typed, from the LLM error tree
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("rate_limited", RateLimitedError),
        ("unavailable", ProviderUnavailableError),
        ("bad_request", ProviderRequestError),
    ],
)
async def test_failures_map_to_the_llm_error_tree(
    harness: TranscriptionHarness, kind: ErrorKind, expected: type[LLMError]
) -> None:
    provider = harness.make_provider()
    harness.arrange_error(kind)

    with pytest.raises(expected):
        await provider.transcribe(make_clip())


async def test_a_rate_limit_carries_the_providers_retry_hint(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_error("rate_limited")

    with pytest.raises(RateLimitedError) as excinfo:
        await provider.transcribe(make_clip())

    assert excinfo.value.retry_after_s == RETRY_AFTER_S


@pytest.mark.parametrize("kind", ["rate_limited", "unavailable", "bad_request"])
async def test_no_secret_leaks_into_an_error_message(
    harness: TranscriptionHarness, kind: ErrorKind
) -> None:
    provider = harness.make_provider(api_key=_SECRET)
    harness.arrange_error(kind)

    with pytest.raises(LLMError) as excinfo:
        await provider.transcribe(make_clip())

    assert _SECRET not in str(excinfo.value)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Push-to-talk: stream() is one clip made of its frames
# ──────────────────────────────────────────────────────────────────────────────


async def test_stream_transcribes_its_frames_as_one_upload(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_answer(verbose=True)
    wav = silent_wav(SPOKEN_DURATION_S)
    frames = (
        AudioChunk(data=wav[:1_000], media_type=AudioMediaType.WAV),
        AudioChunk(data=wav[1_000:], media_type=AudioMediaType.WAV),
    )

    transcript = await provider.stream(_frames(*frames))

    assert transcript.text == SPOKEN_TEXT
    assert harness.uploads() == 1


async def test_a_stream_with_no_audio_is_refused_before_any_upload(
    harness: TranscriptionHarness,
) -> None:
    provider = harness.make_provider()

    with pytest.raises(InvalidAudioClipError):
        await provider.stream(_frames())

    assert harness.uploads() == 0


# ──────────────────────────────────────────────────────────────────────────────
# 5. Health and closing
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", [HealthState.HEALTHY, HealthState.DEGRADED, HealthState.DOWN])
async def test_health_reports_the_arranged_state(
    harness: TranscriptionHarness, state: HealthState
) -> None:
    if state not in harness.supported_health_states():
        pytest.skip(f"{type(harness).__name__} cannot honestly simulate {state.value}.")
    provider = harness.make_provider()
    harness.arrange_health(state)

    health = await provider.health()

    assert health.state is state


async def test_aclose_is_idempotent(harness: TranscriptionHarness) -> None:
    provider = harness.make_provider()

    await provider.aclose()
    await provider.aclose()
