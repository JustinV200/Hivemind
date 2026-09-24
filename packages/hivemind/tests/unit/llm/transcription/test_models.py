"""Tests for hivemind.llm.transcription.models: AudioClip, AudioChunk and the transcript models.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/models.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.models for the module under test.
"""

from __future__ import annotations

import pytest
from builders.audio import SAMPLE_RATE, make_tone_clip, silence_pcm, tone_pcm
from pydantic import ValidationError

from hivemind.llm.transcription.models import (
    MAX_CLIP_BYTES,
    WAV_MEDIA_TYPE,
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptSegment,
)
from hivemind.llm.transcription.wav import encode_wav

# ──────────────────────────────────────────────────────────────────────────────
# AudioClip
# ──────────────────────────────────────────────────────────────────────────────


def test_audio_clip_from_wav_reads_every_stated_fact_from_the_header() -> None:
    data = encode_wav(silence_pcm(1.5, channels=2), SAMPLE_RATE, 2)

    clip = AudioClip.from_wav(data)

    assert clip.sample_rate == SAMPLE_RATE
    assert clip.channels == 2
    assert clip.duration_s == pytest.approx(1.5)
    assert clip.media_type == WAV_MEDIA_TYPE


def test_audio_clip_from_pcm_wraps_the_frames_in_a_wav_header() -> None:
    clip = AudioClip.from_pcm(tone_pcm(0.5), SAMPLE_RATE, 1)

    assert clip.data.startswith(b"RIFF")
    assert clip.duration_s == pytest.approx(0.5)


def test_audio_clip_rejects_a_duration_its_frames_contradict() -> None:
    clip = make_tone_clip(1.0)

    with pytest.raises(ValidationError, match="its frames last"):
        AudioClip(data=clip.data, sample_rate=SAMPLE_RATE, channels=1, duration_s=0.5)


def test_audio_clip_rejects_a_sample_rate_its_header_contradicts() -> None:
    clip = make_tone_clip(1.0)

    with pytest.raises(ValidationError, match="WAV header says"):
        AudioClip(data=clip.data, sample_rate=8_000, channels=1, duration_s=1.0)


def test_audio_clip_rejects_bytes_that_are_not_a_wav_file() -> None:
    with pytest.raises(ValidationError, match="not a readable PCM WAV file"):
        AudioClip(data=b"garbage" * 10, sample_rate=SAMPLE_RATE, channels=1, duration_s=0.0)


def test_audio_clip_rejects_more_than_max_clip_bytes() -> None:
    with pytest.raises(ValidationError):
        AudioClip(data=bytes(MAX_CLIP_BYTES + 1), sample_rate=SAMPLE_RATE, channels=1, duration_s=0)


def test_audio_clip_repr_never_shows_the_audio_bytes() -> None:
    clip = make_tone_clip(0.1)

    assert "data" not in repr(clip)
    assert "RIFF" not in repr(clip)


def test_audio_clip_round_trips_through_json() -> None:
    clip = make_tone_clip(0.25)

    restored = AudioClip.model_validate_json(clip.model_dump_json())

    assert restored == clip


def test_audio_clip_is_frozen() -> None:
    clip = make_tone_clip(0.1)

    with pytest.raises(ValidationError):
        clip.duration_s = 9.0  # type: ignore[misc]  # The assignment itself is the test.


# ──────────────────────────────────────────────────────────────────────────────
# AudioChunk
# ──────────────────────────────────────────────────────────────────────────────


def test_audio_chunk_accepts_whole_frames() -> None:
    chunk = AudioChunk(pcm=silence_pcm(0.1, channels=2), sample_rate=SAMPLE_RATE, channels=2)

    assert chunk.channels == 2


def test_audio_chunk_rejects_a_partial_frame() -> None:
    with pytest.raises(ValidationError, match="whole number"):
        AudioChunk(pcm=b"\x00\x00\x00", sample_rate=SAMPLE_RATE, channels=1)


def test_audio_chunk_rejects_more_channels_than_stereo() -> None:
    with pytest.raises(ValidationError):
        AudioChunk(pcm=b"", sample_rate=SAMPLE_RATE, channels=6)


def test_audio_chunk_round_trips_through_json_and_hides_its_frames() -> None:
    chunk = AudioChunk(pcm=tone_pcm(0.05), sample_rate=SAMPLE_RATE, channels=1)

    restored = AudioChunk.model_validate_json(chunk.model_dump_json())

    assert restored == chunk
    assert "pcm" not in repr(chunk)


# ──────────────────────────────────────────────────────────────────────────────
# TranscriptSegment and Transcript
# ──────────────────────────────────────────────────────────────────────────────


def test_transcript_segment_rejects_an_end_before_its_start() -> None:
    with pytest.raises(ValidationError, match="before it starts"):
        TranscriptSegment(start_s=1.0, end_s=0.5, text="late")


def test_transcript_segment_rejects_a_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        TranscriptSegment(start_s=0.0, end_s=0.5, text="sure", confidence=1.5)


def test_transcript_from_segments_orders_them_and_joins_their_text() -> None:
    later = TranscriptSegment(start_s=0.5, end_s=0.9, text="the hive.")
    earlier = TranscriptSegment(start_s=0.0, end_s=0.4, text="Hello from")

    transcript = Transcript.from_segments([later, earlier], language="en", duration_s=1.0)

    assert transcript.text == "Hello from the hive."
    assert [segment.start_s for segment in transcript.segments] == [0.0, 0.5]


def test_transcript_from_no_segments_is_silence() -> None:
    transcript = Transcript.from_segments([], language=None, duration_s=2.0)

    assert transcript.text == ""
    assert transcript.segments == ()


def test_transcript_rejects_segments_out_of_time_order() -> None:
    later = TranscriptSegment(start_s=0.5, end_s=0.9, text="b")
    earlier = TranscriptSegment(start_s=0.0, end_s=0.4, text="a")

    with pytest.raises(ValidationError, match="time order"):
        Transcript(text="b a", duration_s=1.0, segments=(later, earlier))


@pytest.mark.parametrize("language", ["EN", "english", "e", "en-US"])
def test_transcript_rejects_a_language_that_is_not_a_lowercase_iso_code(language: str) -> None:
    with pytest.raises(ValidationError):
        Transcript(text="", language=language, duration_s=0.0)


def test_transcript_round_trips_through_json() -> None:
    segment = TranscriptSegment(start_s=0.0, end_s=0.4, text="hi", confidence=0.8)
    transcript = Transcript.from_segments([segment], language="en", duration_s=0.5)

    restored = Transcript.model_validate_json(transcript.model_dump_json())

    assert restored == transcript
