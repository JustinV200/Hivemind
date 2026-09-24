"""Read and write the WAV container with the standard library: the one audio format a clip carries.

A transcription (turning speech into text on `ModelSlot.TRANSCRIBER`, the model slot that hears)
needs to know three facts about its audio before any model runs: the sample rate, the channel
count and how long it is. The WAV container states all three in a small header, and the standard
library's `wave` module reads that header without a codec or a native library, which is why a
clip crossing the transcription boundary is a WAV file and nothing else (ADR-0033). This module
is the only place a WAV header is parsed or written: `read_wav_header` for a clip that arrives
whole, `encode_wav` for raw PCM frames a push-to-talk stream delivered piece by piece.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Called by `hivemind.llm.transcription.models` (to validate an `AudioClip` and to build one
    from PCM) and, through those models, by every transcription adapter and the buffered stream
    helper. Calls into the standard library only.

Key invariants:
    - Nothing here decodes a sample: `read_wav_header` copies the data chunk only to count its
      frames honestly, so a header that claims more audio than the file holds is measured by
      what is really there, never by the claim.
    - `encode_wav` always writes 16-bit PCM (`PCM_SAMPLE_WIDTH_BYTES`), the width microphones
      deliver and every Whisper server accepts; `read_wav_header` accepts any width the standard
      library can read.
    - A malformed file raises `ValueError`, never `wave.Error` or `EOFError`, so a pydantic
      validator calling this reports it as an ordinary validation failure.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for why a clip is a WAV file.
    - hivemind.llm.transcription.models for AudioClip, the model this module validates.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

PCM_SAMPLE_WIDTH_BYTES = 2  # 16-bit signed PCM: what a microphone delivers and Whisper expects.
WAV_HEADER_BYTES = 44  # The canonical RIFF/fmt/data header `wave` writes in front of the frames.

__all__ = [
    "PCM_SAMPLE_WIDTH_BYTES",
    "WAV_HEADER_BYTES",
    "WavHeader",
    "encode_wav",
    "read_wav_header",
]


@dataclass(frozen=True, slots=True)
class WavHeader:
    """What one WAV file says about the audio inside it, measured from the file itself."""

    sample_rate: int  # Frames per second.
    channels: int  # Interleaved channels in every frame.
    sample_width: int  # Bytes per sample, per channel.
    frame_count: int  # Whole frames actually present in the data chunk.

    @property
    def duration_s(self) -> float:
        """Return how long the audio lasts, in seconds: frames over frames-per-second."""
        return self.frame_count / self.sample_rate


def read_wav_header(data: bytes) -> WavHeader:
    """Parse `data` as a PCM WAV file and measure the audio it really holds.

    Args:
        data: A whole WAV file, header and frames, as bytes.

    Returns:
        The file's sample rate, channel count and sample width, and the number of whole frames
        its data chunk actually carries (which can be fewer than the header claims when the file
        was cut short).

    Raises:
        ValueError: `data` is not a PCM WAV file the standard library can read, or it states a
            sample rate of zero, which no real recording has.
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            sample_rate = reader.getframerate()
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            # Count what is really there rather than trusting getnframes(): a truncated upload
            # keeps its original header, and metering must never bill audio that never arrived.
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a readable PCM WAV file: {exc}") from exc
    if sample_rate <= 0:
        raise ValueError(f"WAV header states a sample rate of {sample_rate}; it must be positive.")
    return WavHeader(
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        frame_count=len(frames) // (sample_width * channels),
    )


def encode_wav(pcm: bytes, sample_rate: int, channels: int) -> bytes:
    """Wrap raw 16-bit PCM frames in a WAV header.

    Args:
        pcm: Interleaved 16-bit little-endian samples, whole frames only.
        sample_rate: Frames per second the frames were recorded at. Must be > 0.
        channels: Channels per frame. Must be >= 1.

    Returns:
        A complete WAV file: `WAV_HEADER_BYTES` of header followed by `pcm` unchanged.

    Raises:
        ValueError: `sample_rate` or `channels` is one the WAV format cannot state.
    """
    buffer = io.BytesIO()
    try:
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
            writer.setframerate(sample_rate)
            writer.writeframes(pcm)
    except wave.Error as exc:
        raise ValueError(f"cannot write a WAV header for these frames: {exc}") from exc
    return buffer.getvalue()
