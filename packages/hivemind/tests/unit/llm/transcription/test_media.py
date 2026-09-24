"""Tests for hivemind.llm.transcription.media: AudioMediaType and its parsing.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/media.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.media for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.llm.transcription import AudioMediaType, ClipProblem, InvalidAudioClipError


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("audio/wav", AudioMediaType.WAV),
        ("audio/x-wav", AudioMediaType.WAV),
        ("AUDIO/WAVE", AudioMediaType.WAV),
        ("audio/webm;codecs=opus", AudioMediaType.WEBM_OPUS),
        ('audio/ogg; codecs="opus"', AudioMediaType.OGG_OPUS),
        ("audio/opus", AudioMediaType.OGG_OPUS),
        ("audio/mpeg", AudioMediaType.MP3),
        ("audio/mp3", AudioMediaType.MP3),
        ("audio/mp4;codecs=mp4a.40.2", AudioMediaType.M4A),
        ("audio/x-m4a", AudioMediaType.M4A),
    ],
)
def test_parse_folds_every_accepted_spelling_onto_its_format(
    label: str, expected: AudioMediaType
) -> None:
    assert AudioMediaType.parse(label) is expected


@pytest.mark.parametrize("label", ["video/mp4", "audio/flac", "text/plain", ""])
def test_parse_refuses_a_format_the_hive_does_not_accept(label: str) -> None:
    with pytest.raises(InvalidAudioClipError) as excinfo:
        AudioMediaType.parse(label)

    assert excinfo.value.problem is ClipProblem.UNSUPPORTED_FORMAT


@pytest.mark.parametrize("label", ["audio/webm;codecs=vorbis", "audio/ogg; codecs=flac"])
def test_parse_refuses_an_opus_container_declaring_another_codec(label: str) -> None:
    with pytest.raises(InvalidAudioClipError) as excinfo:
        AudioMediaType.parse(label)

    assert excinfo.value.problem is ClipProblem.UNSUPPORTED_FORMAT


def test_every_format_names_its_upload_extension() -> None:
    extensions = {format_: format_.extension for format_ in AudioMediaType}

    assert extensions == {
        AudioMediaType.WAV: "wav",
        AudioMediaType.OGG_OPUS: "ogg",
        AudioMediaType.WEBM_OPUS: "webm",
        AudioMediaType.MP3: "mp3",
        AudioMediaType.M4A: "m4a",
    }


def test_a_formats_value_is_its_own_canonical_media_type() -> None:
    for format_ in AudioMediaType:
        assert AudioMediaType.parse(format_.value) is format_
