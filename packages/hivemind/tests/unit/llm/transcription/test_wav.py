"""Tests for hivemind.llm.transcription.wav: reading a clip's duration from its RIFF header.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/wav.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.wav for the module under test.
    - builders.audio for silent_wav, the stdlib-written fixture.
"""

from __future__ import annotations

import struct

import pytest
from builders.audio import silent_wav

from hivemind.llm.transcription import wav_duration_s

_IEEE_FLOAT_FORMAT_TAG = 3  # A float WAV: what the stdlib wave module refuses to read.


def _chunk(chunk_id: bytes, body: bytes, declared: int | None = None) -> bytes:
    """Build one RIFF chunk, padded to a word boundary; `declared` overrides its size field."""
    size = len(body) if declared is None else declared
    pad = b"\x00" if len(body) % 2 else b""
    return struct.pack("<4sI", chunk_id, size) + body + pad


def _fmt(byte_rate: int, format_tag: int = 1) -> bytes:
    """Build a `fmt ` chunk declaring `byte_rate` (mono; the other fields only need to parse)."""
    return _chunk(b"fmt ", struct.pack("<HHIIHH", format_tag, 1, byte_rate, byte_rate, 1, 8))


def _riff(*chunks: bytes) -> bytes:
    """Wrap `chunks` in a RIFF/WAVE header."""
    body = b"WAVE" + b"".join(chunks)
    return struct.pack("<4sI", b"RIFF", len(body)) + body


@pytest.mark.parametrize(("seconds", "rate"), [(1.0, 16_000), (2.5, 8_000), (0.25, 44_100)])
def test_duration_of_a_stdlib_written_wav_is_what_was_written(seconds: float, rate: int) -> None:
    assert wav_duration_s(silent_wav(seconds, rate)) == pytest.approx(seconds)


def test_a_float_wav_the_stdlib_would_refuse_is_still_measured() -> None:
    clip = _riff(_fmt(4_000, _IEEE_FLOAT_FORMAT_TAG), _chunk(b"data", b"\x00" * 8_000))

    assert wav_duration_s(clip) == 2.0


def test_chunks_before_data_are_skipped_honouring_their_odd_size_padding() -> None:
    clip = _riff(_fmt(1_000), _chunk(b"LIST", b"abc"), _chunk(b"data", b"\x00" * 500))

    assert wav_duration_s(clip) == 0.5


@pytest.mark.parametrize("placeholder", [0, 0xFFFF_FFFF])
def test_a_streaming_placeholder_data_size_is_measured_by_the_bytes_present(
    placeholder: int,
) -> None:
    clip = _riff(_fmt(1_000), _chunk(b"data", b"\x00" * 1_500, declared=placeholder))

    assert wav_duration_s(clip) == 1.5


def test_a_truncated_clip_is_measured_by_the_bytes_present() -> None:
    clip = _riff(_fmt(1_000), _chunk(b"data", b"\x00" * 250, declared=10_000))

    assert wav_duration_s(clip) == 0.25


@pytest.mark.parametrize(
    ("clip", "reason"),
    [
        (b"RIFF", "header bytes"),
        (b"RIFF\x00\x00\x00\x00WAVX" + b"\x00" * 8, "RIFF/WAVE"),
        (_riff(_chunk(b"data", b"\x00" * 10)), "before any fmt"),
        (_riff(_fmt(1_000)), "no data chunk"),
        (_riff(_fmt(0), _chunk(b"data", b"\x00" * 10)), "byte rate of zero"),
        (_riff(_chunk(b"fmt ", b"\x01\x00")), "truncated"),
    ],
)
def test_an_unreadable_header_raises_value_error(clip: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        wav_duration_s(clip)
