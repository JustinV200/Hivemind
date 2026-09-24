"""Tests for hivemind.llm.transcription.fake: FakeTranscription's script, outage and honesty.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/fake.py (codingrules section 3). The protocol-level
    clauses live in contracts/test_transcription_provider_contract.py; these cover what only
    the fake has (its script, its outage switch, its recorded calls).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.fake for the module under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.audio import make_clip, make_transcript

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError, RateLimitedError
from hivemind.llm.transcription import (
    FAKE_DETECTED_LANGUAGE,
    UNSUPPORTED_MEDIA_TYPE_STATUS,
    AudioChunk,
    AudioClip,
    AudioMediaType,
    FakeTranscription,
    TranscriptionCapabilities,
)

_OPUS_BYTES = b"OggS" + b"\x00" * 60  # Opaque compressed bytes: never decoded.


async def _frames(*chunks: AudioChunk) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` as a push-to-talk stream would."""
    for chunk in chunks:
        yield chunk


async def test_a_scripted_string_becomes_a_transcript_spanning_the_clip() -> None:
    fake = FakeTranscription()
    fake.script("open the garage")
    clip = make_clip(2.0)

    transcript = await fake.transcribe(clip)

    assert transcript.text == "open the garage"
    assert transcript.duration_s == clip.duration_s
    assert transcript.language == FAKE_DETECTED_LANGUAGE
    assert [(s.start_s, s.end_s) for s in transcript.segments] == [(0.0, clip.duration_s)]


async def test_scripted_answers_are_returned_in_order_and_errors_raised_on_their_turn() -> None:
    fake = FakeTranscription()
    fake.script(make_transcript("first"), RateLimitedError("fake", retry_after_s=1.0), "third")

    first = await fake.transcribe(make_clip())
    with pytest.raises(RateLimitedError):
        await fake.transcribe(make_clip())
    third = await fake.transcribe(make_clip())

    assert (first.text, third.text) == ("first", "third")


async def test_an_empty_script_raises_provider_unavailable_not_index_error() -> None:
    with pytest.raises(ProviderUnavailableError, match="ran dry"):
        await FakeTranscription().transcribe(make_clip())


async def test_an_outage_fails_every_call_before_recording_it_and_reports_down() -> None:
    fake = FakeTranscription()
    fake.script("never heard")
    fake.set_outage(True)

    with pytest.raises(ProviderUnavailableError):
        await fake.transcribe(make_clip())
    health = await fake.health()

    assert fake.calls == []
    assert health.state is HealthState.DOWN


async def test_recovering_from_an_outage_lets_calls_through_and_reports_healthy() -> None:
    fake = FakeTranscription()
    fake.script("back again")
    fake.set_outage(True)
    fake.set_outage(False)

    transcript = await fake.transcribe(make_clip())

    assert transcript.text == "back again"
    assert (await fake.health()).state is HealthState.HEALTHY


async def test_calls_record_each_clip_with_its_normalised_language_hint() -> None:
    fake = FakeTranscription()
    fake.script("one", "two")
    clip = make_clip()

    await fake.transcribe(clip, "en-GB")
    await fake.transcribe(clip)

    assert [(call.clip, call.language) for call in fake.calls] == [(clip, "en"), (clip, None)]


async def test_a_format_outside_the_declared_capabilities_is_refused_with_415() -> None:
    fake = FakeTranscription(capabilities=TranscriptionCapabilities.none())
    fake.script("unreachable")
    clip = AudioClip.from_upload(_OPUS_BYTES, AudioMediaType.OGG_OPUS, duration_s=1.0)

    with pytest.raises(ProviderRequestError) as excinfo:
        await fake.transcribe(clip)

    assert excinfo.value.status_code == UNSUPPORTED_MEDIA_TYPE_STATUS


async def test_no_segments_and_no_detection_strip_what_the_fake_could_not_produce() -> None:
    fake = FakeTranscription(capabilities=TranscriptionCapabilities.none())
    fake.script(make_transcript("plain words"))

    transcript = await fake.transcribe(make_clip())

    assert transcript.text == "plain words"
    assert transcript.segments == ()
    assert transcript.language is None


async def test_a_language_hint_is_echoed_even_without_detection() -> None:
    fake = FakeTranscription(capabilities=TranscriptionCapabilities.none())
    fake.script("bonjour")

    transcript = await fake.transcribe(make_clip(), "fr-FR")

    assert transcript.language == "fr"


async def test_stream_gathers_the_frames_into_one_clip_and_transcribes_it() -> None:
    fake = FakeTranscription()
    fake.script("push to talk")
    frames = (
        AudioChunk(data=_OPUS_BYTES, media_type=AudioMediaType.WEBM_OPUS, duration_s=0.5),
        AudioChunk(data=_OPUS_BYTES, media_type=AudioMediaType.WEBM_OPUS, duration_s=0.5),
    )

    transcript = await fake.stream(_frames(*frames))

    assert transcript.text == "push to talk"
    assert fake.calls[0].clip.data == _OPUS_BYTES * 2
    assert transcript.duration_s == 1.0


async def test_aclose_only_records_the_close() -> None:
    fake = FakeTranscription()
    fake.script("still answering")

    await fake.aclose()
    await fake.aclose()
    transcript = await fake.transcribe(make_clip())

    assert fake.is_closed
    assert transcript.text == "still answering"
