"""Read a WAV clip's duration from its RIFF header, without decoding a single sample.

A WAV file is the one accepted format whose length the Hive can know for itself: its RIFF header
names the byte rate (in the `fmt ` chunk) and the size of the sample data (the `data` chunk), and
their quotient is the duration in seconds. So for WAV the Hive never has to trust a sender's claim
about how long a clip is -- the claim the Entrance's `max_clip_seconds` cap and per-device audio
budget are measured against. The header is walked by hand with `struct` rather than through the
standard library's `wave` module because `wave` refuses every format tag except integer PCM,
while a recorder's 32-bit float WAV is just as transcribable and its header just as readable.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Called by
    `hivemind.llm.transcription.models.AudioClip` to fill in and check a WAV clip's duration.
    Imports only the standard library.

Key invariants:
    - Pure: bytes in, seconds out; no I/O, no state.
    - Raises `ValueError` for anything that is not a readable RIFF/WAVE header, so a pydantic
      validator can call it directly and have the failure become a `ValidationError`.
    - A `data` chunk whose declared size is a streaming writer's placeholder (0 or 0xFFFFFFFF),
      or larger than the bytes actually present, is measured by the bytes present: a clip is as
      long as the audio that will actually be transcribed.

See Also:
    - hivemind.llm.transcription.models for AudioClip, this module's one caller.
"""

from __future__ import annotations

import struct

_RIFF_HEADER = struct.Struct("<4sI4s")  # "RIFF", the file size minus 8, "WAVE".
_CHUNK_HEADER = struct.Struct("<4sI")  # A chunk's four-byte id and its body size.
# The fixed prefix every `fmt ` chunk starts with: format tag, channels, sample rate, byte rate,
# block align, bits per sample. Only the byte rate is read; the rest is parsed to prove the
# chunk is at least this long.
_FMT_PREFIX = struct.Struct("<HHIIHH")
_BYTE_RATE_INDEX = 3  # The byte rate's position in _FMT_PREFIX's unpacked tuple.
_STREAMING_PLACEHOLDER_SIZES = frozenset({0, 0xFFFF_FFFF})  # What a writer that cannot seek
# back leaves in the data chunk's size field, since it never learned the final length.

__all__ = ["wav_duration_s"]


def wav_duration_s(data: bytes) -> float:
    """Return the duration, in seconds, that a WAV clip's header declares.

    Args:
        data: The whole clip, header included.

    Returns:
        The sample data's size in bytes divided by the byte rate; may be 0.0 for a clip with an
        empty data chunk (the caller decides whether an empty clip is acceptable).

    Raises:
        ValueError: `data` does not start with a RIFF/WAVE header, has no `fmt ` chunk before
            its `data` chunk, has no `data` chunk, or declares a byte rate of zero.
    """
    if len(data) < _RIFF_HEADER.size:
        raise ValueError(f"a WAV clip needs at least {_RIFF_HEADER.size} header bytes")
    riff, _, wave = _RIFF_HEADER.unpack_from(data, 0)
    if riff != b"RIFF" or wave != b"WAVE":
        raise ValueError("the clip does not start with a RIFF/WAVE header")
    byte_rate, data_bytes = _scan_chunks(data)
    if byte_rate == 0:
        raise ValueError("the WAV header declares a byte rate of zero")
    return data_bytes / byte_rate


def _scan_chunks(data: bytes) -> tuple[int, int]:
    """Walk the chunks after the RIFF header; return `(byte_rate, data_bytes)`.

    Raises:
        ValueError: No `fmt ` chunk precedes the `data` chunk, or there is no `data` chunk.
    """
    byte_rate: int | None = None
    offset = _RIFF_HEADER.size
    # Walk chunk by chunk until the data chunk: `fmt ` must come first (the RIFF/WAVE layout),
    # and chunks after `data` (LIST, cue, ...) carry nothing a duration needs.
    while offset + _CHUNK_HEADER.size <= len(data):
        chunk_id, size = _CHUNK_HEADER.unpack_from(data, offset)
        body = offset + _CHUNK_HEADER.size
        if chunk_id == b"fmt ":
            byte_rate = _read_byte_rate(data, body)
        elif chunk_id == b"data":
            if byte_rate is None:
                raise ValueError("the WAV data chunk comes before any fmt chunk")
            return byte_rate, _data_size(size, len(data) - body)
        # Chunks are word-aligned: an odd-sized body is followed by one pad byte.
        offset = body + size + (size % 2)
    raise ValueError("the WAV clip has no data chunk")


def _read_byte_rate(data: bytes, body: int) -> int:
    """Return the byte rate from a `fmt ` chunk whose body starts at `body`.

    Raises:
        ValueError: The chunk is shorter than the fixed `fmt ` prefix.
    """
    if body + _FMT_PREFIX.size > len(data):
        raise ValueError("the WAV fmt chunk is truncated")
    fields = _FMT_PREFIX.unpack_from(data, body)
    byte_rate: int = fields[_BYTE_RATE_INDEX]
    return byte_rate


def _data_size(declared: int, available: int) -> int:
    """Return how many bytes of sample data the clip really carries.

    Args:
        declared: The data chunk's own size field.
        available: How many bytes actually follow the data chunk's header.
    """
    # A placeholder or an over-long claim (a streamed or truncated clip) is measured by what is
    # really there; an honest declared size is used as-is, ignoring any trailing chunks.
    if declared in _STREAMING_PLACEHOLDER_SIZES or declared > available:
        return available
    return declared
