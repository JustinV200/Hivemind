"""Tests for hivemind.llm.transcription.fake: FakeTranscription, scripted and honestly limited.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/fake.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.fake for the module under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.audio import make_chunks, make_silence_clip, make_tone_clip, tone_pcm

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError, RateLimitedError
from hivemind.llm.transcription.capabilities import TranscriptionCapabilities
from hivemind.llm.transcription.fake import FakeTranscription, FakeTranscriptionCall
from hivemind.llm.transcription.models import AudioChunk, Transcript, TranscriptSegment


def _transcript(text: str) -> Transcript:
    """Build a one-segment transcript saying `text` over the first half-second."""
    segment = TranscriptSegment(start_s=0.0, end_s=0.5, text=text)
    return Transcript.from_segments([segment], language="en", duration_s=1.0)


async def _iterate(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` in order."""
    for chunk in chunks:
        yield chunk


async def test_fake_transcription_answers_scripted_transcripts_in_order() -> None:
    fake = FakeTranscription()
    fake.script(_transcript("first"), _transcript("second"))

    first = await fake.transcribe(make_tone_clip())
    second = await fake.transcribe(make_tone_clip())

    assert (first.text, second.text) == ("first", "second")


async def test_fake_transcription_answers_silence_spanning_the_clip_once_the_script_is_dry() -> (
    None
):
    fake = FakeTranscription()

    transcript = await fake.transcribe(make_silence_clip(2.0), "de")

    assert transcript.text == ""
    assert transcript.segments == ()
    assert transcript.duration_s == pytest.approx(2.0)
    assert transcript.language == "de"


async def test_fake_transcription_answers_its_given_default_once_the_script_is_dry() -> None:
    fake = FakeTranscription(default=_transcript("always"))

    assert (await fake.transcribe(make_tone_clip())).text == "always"


async def test_fake_transcription_reports_the_clips_duration_and_the_callers_hint() -> None:
    fake = FakeTranscription()
    scripted = _transcript("hallo")  # Scripted as English, over a one-second clip.
    fake.script(scripted)

    transcript = await fake.transcribe(make_tone_clip(2.0), "de")

    assert (transcript.language, transcript.duration_s) == ("de", 2.0)
    assert transcript.segments == scripted.segments


async def test_fake_transcription_returns_a_scripted_answer_unchanged_when_it_agrees() -> None:
    fake = FakeTranscription()
    scripted = _transcript("hello")
    fake.script(scripted)

    assert await fake.transcribe(make_tone_clip(1.0), "en") is scripted


async def test_fake_transcription_raises_a_scripted_error_on_its_turn() -> None:
    fake = FakeTranscription()
    fake.script(RateLimitedError("fake", retry_after_s=1.0), _transcript("after"))

    with pytest.raises(RateLimitedError):
        await fake.transcribe(make_tone_clip())
    assert (await fake.transcribe(make_tone_clip())).text == "after"


async def test_fake_transcription_records_each_call_without_the_audio() -> None:
    fake = FakeTranscription()
    clip = make_tone_clip(0.5)

    await fake.transcribe(clip, "en")

    assert fake.calls == [FakeTranscriptionCall(clip.duration_s, len(clip.data), "en")]


async def test_fake_transcription_refuses_a_clip_past_its_declared_ceiling() -> None:
    fake = FakeTranscription(capabilities=TranscriptionCapabilities(max_clip_s=0.5))

    with pytest.raises(ProviderRequestError):
        await fake.transcribe(make_tone_clip(1.0))
    assert fake.calls == []


async def test_fake_transcription_outage_raises_and_reports_down() -> None:
    fake = FakeTranscription()
    fake.set_outage(True)

    with pytest.raises(ProviderUnavailableError):
        await fake.transcribe(make_tone_clip())
    assert (await fake.health()).state is HealthState.DOWN
    assert fake.calls == []


async def test_fake_transcription_reports_healthy_without_an_outage() -> None:
    assert (await FakeTranscription().health()).state is HealthState.HEALTHY


async def test_fake_transcription_stream_buffers_then_transcribes_once() -> None:
    fake = FakeTranscription()
    fake.script(_transcript("streamed"))
    pcm = tone_pcm(1.0)

    segments = [segment async for segment in fake.stream(_iterate(make_chunks(pcm)), "en")]

    assert [segment.text for segment in segments] == ["streamed"]
    assert len(fake.calls) == 1
    assert fake.calls[0].duration_s == pytest.approx(1.0)
