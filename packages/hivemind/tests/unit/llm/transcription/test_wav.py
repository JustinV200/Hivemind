"""Tests for hivemind.llm.transcription.wav: reading and writing the WAV container.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/wav.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.wav for the module under test.
"""

from __future__ import annotations

import pytest
from builders.audio import SAMPLE_RATE, silence_pcm, tone_pcm

from hivemind.llm.transcription.wav import (
    PCM_SAMPLE_WIDTH_BYTES,
    WAV_HEADER_BYTES,
    encode_wav,
    read_wav_header,
)


def test_encode_wav_prefixes_the_frames_with_a_canonical_header() -> None:
    pcm = tone_pcm(0.5)

    data = encode_wav(pcm, SAMPLE_RATE, 1)

    assert len(data) == WAV_HEADER_BYTES + len(pcm)
    assert data.endswith(pcm)


def test_read_wav_header_measures_what_encode_wav_wrote() -> None:
    data = encode_wav(silence_pcm(2.0, channels=2), SAMPLE_RATE, 2)

    header = read_wav_header(data)

    assert header.sample_rate == SAMPLE_RATE
    assert header.channels == 2
    assert header.sample_width == PCM_SAMPLE_WIDTH_BYTES
    assert header.frame_count == 2 * SAMPLE_RATE
    assert header.duration_s == pytest.approx(2.0)


def test_read_wav_header_counts_the_frames_really_present_in_a_truncated_file() -> None:
    # The header still claims a whole second; only a quarter of the frames arrived.
    whole = encode_wav(silence_pcm(1.0), SAMPLE_RATE, 1)
    truncated = whole[: WAV_HEADER_BYTES + SAMPLE_RATE // 4 * PCM_SAMPLE_WIDTH_BYTES]

    header = read_wav_header(truncated)

    assert header.frame_count == SAMPLE_RATE // 4
    assert header.duration_s == pytest.approx(0.25)


def test_read_wav_header_accepts_an_empty_data_chunk_as_zero_seconds() -> None:
    header = read_wav_header(encode_wav(b"", SAMPLE_RATE, 1))

    assert header.frame_count == 0
    assert header.duration_s == 0.0


@pytest.mark.parametrize("data", [b"", b"RIFF", b"not a wav file at all" * 4])
def test_read_wav_header_rejects_bytes_that_are_not_a_wav_file(data: bytes) -> None:
    with pytest.raises(ValueError, match="not a readable PCM WAV file"):
        read_wav_header(data)


def test_read_wav_header_rejects_a_zero_sample_rate() -> None:
    # Patch the header's sample-rate field (bytes 24-27, little-endian) to zero by hand: the
    # standard library itself refuses to write one.
    data = bytearray(encode_wav(silence_pcm(0.1), SAMPLE_RATE, 1))
    data[24:28] = bytes(4)

    with pytest.raises(ValueError, match="sample rate"):
        read_wav_header(bytes(data))


@pytest.mark.parametrize(("sample_rate", "channels"), [(0, 1), (SAMPLE_RATE, 0)])
def test_encode_wav_rejects_a_format_the_header_cannot_state(
    sample_rate: int, channels: int
) -> None:
    with pytest.raises(ValueError, match="cannot write a WAV header"):
        encode_wav(b"", sample_rate, channels)
