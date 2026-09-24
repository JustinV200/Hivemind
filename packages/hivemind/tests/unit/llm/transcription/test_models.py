"""Tests for hivemind.llm.transcription.models: clips, chunks, transcripts and their bounds.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/models.py (codingrules section 3). Covers codingrules
    14.3's model rules: a round trip for every boundary model and a rejection of malformed input.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.models for the module under test.
    - builders.audio for the generated WAV fixtures.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator

import pytest
from builders.audio import make_clip, make_transcript, silent_wav
from pydantic import ValidationError

from hivemind.llm.transcription import (
    MAX_CLIP_BYTES,
    MAX_CLIP_SECONDS,
    AudioChunk,
    AudioClip,
    AudioMediaType,
    ClipProblem,
    InvalidAudioClipError,
    Transcript,
    TranscriptSegment,
    clip_from_chunks,
    normalise_language,
)
from hivemind.llm.transcription import models as models_module

_OPUS_BYTES = b"OggS" + b"\x00" * 60  # Opaque compressed bytes: never decoded, only carried.


async def _frames(*chunks: AudioChunk) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` as a push-to-talk stream would."""
    for chunk in chunks:
        yield chunk


# ──────────────────────────────────────────────────────────────────────────────
# Round trips (codingrules 14.3)
# ──────────────────────────────────────────────────────────────────────────────


def test_a_wav_clip_round_trips_through_json_with_its_bytes_intact() -> None:
    clip = make_clip(0.5)

    assert AudioClip.model_validate_json(clip.model_dump_json()) == clip


def test_a_compressed_clip_round_trips_through_json() -> None:
    clip = AudioClip.from_upload(_OPUS_BYTES, "audio/ogg;codecs=opus", duration_s=3.0)

    assert AudioClip.model_validate_json(clip.model_dump_json()) == clip


def test_a_transcript_round_trips_through_json() -> None:
    transcript = make_transcript("lights on", seconds=2.0)

    assert Transcript.model_validate_json(transcript.model_dump_json()) == transcript


# ──────────────────────────────────────────────────────────────────────────────
# from_upload: durations, ceilings and refusals
# ──────────────────────────────────────────────────────────────────────────────


def test_from_upload_reads_a_wavs_duration_from_its_header_ignoring_the_senders_claim() -> None:
    clip = AudioClip.from_upload(silent_wav(1.5), "audio/x-wav", duration_s=99.0)

    assert clip.media_type is AudioMediaType.WAV
    assert clip.duration_s == pytest.approx(1.5)


def test_from_upload_takes_a_compressed_clips_duration_from_its_sender() -> None:
    clip = AudioClip.from_upload(_OPUS_BYTES, "audio/webm;codecs=opus", duration_s=4.25)

    assert clip.media_type is AudioMediaType.WEBM_OPUS
    assert clip.duration_s == 4.25


@pytest.mark.parametrize(
    ("data", "media_type", "duration_s", "problem"),
    [
        (b"", "audio/wav", None, ClipProblem.MALFORMED),
        (b"not a wav header at all", "audio/wav", None, ClipProblem.MALFORMED),
        (_OPUS_BYTES, "audio/flac", 1.0, ClipProblem.UNSUPPORTED_FORMAT),
        (_OPUS_BYTES, "audio/ogg", None, ClipProblem.MISSING_DURATION),
        (_OPUS_BYTES, "audio/ogg", -1.0, ClipProblem.MALFORMED),
        (_OPUS_BYTES, "audio/ogg", math.nan, ClipProblem.MALFORMED),
        (_OPUS_BYTES, "audio/ogg", MAX_CLIP_SECONDS + 1, ClipProblem.TOO_LONG),
    ],
)
def test_from_upload_refuses_a_malformed_clip_with_the_matching_problem(
    data: bytes, media_type: str, duration_s: float | None, problem: ClipProblem
) -> None:
    with pytest.raises(InvalidAudioClipError) as excinfo:
        AudioClip.from_upload(data, media_type, duration_s)

    assert excinfo.value.problem is problem


def test_from_upload_refuses_a_wav_whose_header_runs_past_the_ceiling() -> None:
    # A low sample rate keeps a clip longer than the ceiling to about a megabyte.
    too_long = silent_wav(MAX_CLIP_SECONDS + 1, sample_rate=1_000)

    with pytest.raises(InvalidAudioClipError) as excinfo:
        AudioClip.from_upload(too_long, AudioMediaType.WAV)

    assert excinfo.value.problem is ClipProblem.TOO_LONG


def test_from_upload_refuses_a_clip_past_the_byte_ceiling() -> None:
    with pytest.raises(InvalidAudioClipError) as excinfo:
        AudioClip.from_upload(b"\x00" * (MAX_CLIP_BYTES + 1), "audio/mpeg", duration_s=1.0)

    assert excinfo.value.problem is ClipProblem.TOO_LARGE


def test_a_wav_clip_built_directly_must_state_its_headers_own_duration() -> None:
    with pytest.raises(ValidationError, match="must match its header"):
        AudioClip(data=silent_wav(1.0), media_type=AudioMediaType.WAV, duration_s=0.1)


def test_a_clip_never_shows_its_audio_bytes_in_its_repr() -> None:
    clip = AudioClip.from_upload(b"secret-voice" + _OPUS_BYTES, "audio/ogg", duration_s=1.0)

    assert "secret-voice" not in repr(clip)


# ──────────────────────────────────────────────────────────────────────────────
# Transcript and TranscriptSegment
# ──────────────────────────────────────────────────────────────────────────────


def test_a_segment_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(ValidationError, match="cannot end"):
        TranscriptSegment(start_s=2.0, end_s=1.0, text="backwards")


def test_a_transcript_refuses_an_empty_language_name() -> None:
    with pytest.raises(ValidationError):
        Transcript(text="hi", language="", duration_s=1.0)


def test_a_transcript_never_shows_its_text_in_its_repr() -> None:
    transcript = make_transcript("my private words")

    assert "my private words" not in repr(transcript)


# ──────────────────────────────────────────────────────────────────────────────
# clip_from_chunks: push-to-talk frames into one clip
# ──────────────────────────────────────────────────────────────────────────────


async def test_clip_from_chunks_joins_frames_and_sums_their_durations() -> None:
    first = AudioChunk(data=_OPUS_BYTES, media_type=AudioMediaType.OGG_OPUS, duration_s=0.5)
    second = AudioChunk(data=b"\x01" * 16, media_type=AudioMediaType.OGG_OPUS, duration_s=0.25)

    clip = await clip_from_chunks(_frames(first, second))

    assert clip.data == _OPUS_BYTES + b"\x01" * 16
    assert clip.duration_s == 0.75


async def test_clip_from_chunks_measures_a_wav_stream_by_its_header() -> None:
    wav = silent_wav(1.0)
    head = AudioChunk(data=wav[:100], media_type=AudioMediaType.WAV)
    tail = AudioChunk(data=wav[100:], media_type=AudioMediaType.WAV)

    clip = await clip_from_chunks(_frames(head, tail))

    assert clip.duration_s == pytest.approx(1.0)


async def test_clip_from_chunks_needs_every_frames_duration_for_a_compressed_stream() -> None:
    known = AudioChunk(data=_OPUS_BYTES, media_type=AudioMediaType.WEBM_OPUS, duration_s=1.0)
    unknown = AudioChunk(data=_OPUS_BYTES, media_type=AudioMediaType.WEBM_OPUS)

    with pytest.raises(InvalidAudioClipError) as excinfo:
        await clip_from_chunks(_frames(known, unknown))

    assert excinfo.value.problem is ClipProblem.MISSING_DURATION


async def test_clip_from_chunks_refuses_a_stream_that_changes_format() -> None:
    ogg = AudioChunk(data=_OPUS_BYTES, media_type=AudioMediaType.OGG_OPUS, duration_s=1.0)
    webm = AudioChunk(data=_OPUS_BYTES, media_type=AudioMediaType.WEBM_OPUS, duration_s=1.0)

    with pytest.raises(InvalidAudioClipError) as excinfo:
        await clip_from_chunks(_frames(ogg, webm))

    assert excinfo.value.problem is ClipProblem.MALFORMED


async def test_clip_from_chunks_refuses_an_empty_stream() -> None:
    with pytest.raises(InvalidAudioClipError) as excinfo:
        await clip_from_chunks(_frames())

    assert excinfo.value.problem is ClipProblem.MALFORMED


async def test_clip_from_chunks_refuses_a_stream_as_soon_as_it_passes_the_byte_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A small ceiling proves the running check without allocating 25 MB of frames.
    monkeypatch.setattr(models_module, "MAX_CLIP_BYTES", 100)
    frame = AudioChunk(data=b"\x00" * 60, media_type=AudioMediaType.MP3, duration_s=1.0)

    with pytest.raises(InvalidAudioClipError) as excinfo:
        await clip_from_chunks(_frames(frame, frame))

    assert excinfo.value.problem is ClipProblem.TOO_LARGE


# ──────────────────────────────────────────────────────────────────────────────
# normalise_language
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("en", "en"),
        ("en-US", "en"),
        ("EN_gb", "en"),
        (" fil ", "fil"),
        ("zh-Hant-TW", "zh"),
        (None, None),
        ("english", None),
        ("e", None),
        ("12", None),
        ("", None),
    ],
)
def test_normalise_language_keeps_a_plausible_primary_subtag_and_drops_the_rest(
    hint: str | None, expected: str | None
) -> None:
    assert normalise_language(hint) == expected
