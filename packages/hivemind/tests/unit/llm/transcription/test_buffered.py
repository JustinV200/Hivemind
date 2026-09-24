"""Tests for hivemind.llm.transcription.buffered: push-to-talk by buffering, then one transcription.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/buffered.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.buffered for the module under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from builders.audio import SAMPLE_RATE, make_chunks, silence_pcm, tone_pcm

from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.transcription.buffered import (
    FORMAT_MISMATCH_STATUS_CODE,
    STREAM_TOO_LARGE_STATUS_CODE,
    collect_clip,
    stream_by_buffering,
)
from hivemind.llm.transcription.models import (
    MAX_PCM_BYTES,
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptSegment,
)


async def _iterate(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` in order, as a microphone stream would."""
    for chunk in chunks:
        yield chunk


@dataclass
class _RecordingTranscriber:
    """A stand-in `transcribe` that records each clip it gets and answers two segments."""

    clips: list[AudioClip] = field(default_factory=list)
    languages: list[str | None] = field(default_factory=list)

    async def __call__(self, clip: AudioClip, language: str | None) -> Transcript:
        """Record the call; answer "one two" over the clip's first half-second."""
        self.clips.append(clip)
        self.languages.append(language)
        segments = [
            TranscriptSegment(start_s=0.0, end_s=0.2, text="one"),
            TranscriptSegment(start_s=0.2, end_s=0.4, text="two"),
        ]
        return Transcript.from_segments(segments, language=language, duration_s=clip.duration_s)


async def test_collect_clip_reassembles_every_chunk_in_order() -> None:
    pcm = tone_pcm(1.0)

    clip = await collect_clip(_iterate(make_chunks(pcm, pieces=5)), "p")

    assert clip is not None
    assert clip.data.endswith(pcm)
    assert clip.duration_s == pytest.approx(1.0)


async def test_collect_clip_returns_none_for_an_empty_stream() -> None:
    assert await collect_clip(_iterate([]), "p") is None


async def test_collect_clip_returns_none_for_chunks_holding_no_frames() -> None:
    empty = AudioChunk(pcm=b"", sample_rate=SAMPLE_RATE, channels=1)

    assert await collect_clip(_iterate([empty, empty]), "p") is None


async def test_collect_clip_refuses_a_chunk_that_changes_the_format() -> None:
    first = AudioChunk(pcm=silence_pcm(0.1), sample_rate=SAMPLE_RATE, channels=1)
    changed = AudioChunk(pcm=silence_pcm(0.1, sample_rate=8_000), sample_rate=8_000, channels=1)

    with pytest.raises(ProviderRequestError) as caught:
        await collect_clip(_iterate([first, changed]), "p")

    assert caught.value.status_code == FORMAT_MISMATCH_STATUS_CODE
    assert caught.value.error_type == "audio_format_mismatch"


async def test_collect_clip_refuses_a_stream_that_outgrows_one_clip() -> None:
    # Two chunks that each fit, but not together: the bound trips on the second one.
    half = MAX_PCM_BYTES // 2 + 2
    big = AudioChunk(pcm=bytes(half), sample_rate=SAMPLE_RATE, channels=1)

    with pytest.raises(ProviderRequestError) as caught:
        await collect_clip(_iterate([big, big]), "p")

    assert caught.value.status_code == STREAM_TOO_LARGE_STATUS_CODE


async def test_stream_by_buffering_transcribes_once_and_yields_every_segment() -> None:
    transcriber = _RecordingTranscriber()

    segments = [
        segment
        async for segment in stream_by_buffering(
            transcriber, _iterate(make_chunks(tone_pcm(0.5))), "en", "p"
        )
    ]

    assert [segment.text for segment in segments] == ["one", "two"]
    assert len(transcriber.clips) == 1
    assert transcriber.languages == ["en"]


async def test_stream_by_buffering_calls_nothing_for_an_empty_stream() -> None:
    transcriber = _RecordingTranscriber()

    segments = [
        segment async for segment in stream_by_buffering(transcriber, _iterate([]), None, "p")
    ]

    assert segments == []
    assert transcriber.clips == []
