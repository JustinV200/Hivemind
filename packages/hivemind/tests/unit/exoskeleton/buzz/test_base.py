"""Unit tests for hivemind.exoskeleton.buzz.base: Recording."""

from __future__ import annotations

import io
import wave

from hivemind.exoskeleton.buzz.base import Recording


def test_from_pcm_wraps_samples_in_a_wav_whose_header_agrees() -> None:
    recording = Recording.from_pcm(b"\x01\x00" * 16_000, sample_rate=16_000, channels=1)

    with wave.open(io.BytesIO(recording.wav)) as reader:
        assert (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) == (
            16_000,
            1,
            2,
        )
        assert reader.getnframes() == 16_000
    assert recording.duration_s == 1.0


def test_a_torn_last_frame_is_dropped_not_padded() -> None:
    recording = Recording.from_pcm(b"\x00\x00\x00\x00\x00", sample_rate=8_000, channels=2)

    assert recording.duration_s == 1 / 8_000


def test_a_recordings_repr_never_holds_the_audio() -> None:
    recording = Recording.from_pcm(b"\x7f\x7f" * 10, sample_rate=8_000, channels=1)

    assert "RIFF" not in repr(recording)
