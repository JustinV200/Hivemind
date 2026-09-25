"""Buffer push-to-talk into one clip: raw PCM for a transcriber, encoded frames from a device.

A transcriber (an adapter on `ModelSlot.TRANSCRIBER`, the model slot that hears) without native
streaming still has to offer `stream()`, because callers such as the Hive Entrance's push-to-talk
route (roadmap step 10.5f) should never branch on which provider is behind the slot. ADR-0033
settles how: buffer the chunks while the human talks, then transcribe once when they stop, so the
latency is the clip's own length plus one transcription. `collect_clip` does the buffering
(checking that every chunk shares one format and that the whole still fits one clip),
`stream_by_buffering` runs the one transcription and yields its segments; an adapter's `stream`
is then a one-line call into this module. A device's push-to-talk (roadmap step 10.5f) arrives
the other way: a browser recorder's encoded frames (`EncodedAudioChunk`, Opus in WebM or Ogg,
only the first carrying the container header), which `clip_from_chunks` joins into one
`AudioClip` through `AudioClip.from_upload` before any transcriber is involved.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Called by every adapter whose `capabilities.streaming` is False: the fake, faster-whisper and
    the OpenAI-compatible adapter today; `clip_from_chunks` by the Entrance's push-to-talk
    (`hivemind.entrance.voice.talk`). Calls into `hivemind.llm.errors` and this package's
    `errors`, `media` and `models` only.

Key invariants:
    - The transcriber is called at most once per stream, and only after the last chunk arrived:
      no model work, no seat, and no network call while the speaker is still talking.
    - An empty stream (no chunks, or chunks holding no frames) calls nothing and yields nothing.
    - The buffer never grows past `MAX_PCM_BYTES` (`MAX_CLIP_BYTES` for encoded frames): an
      over-long stream is refused as soon as it crosses the bound, not after holding everything.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for "an adapter without native
      streaming buffers the chunks and transcribes once at the end".
    - hivemind.llm.transcription.provider for TranscriptionProvider.stream, the contract served.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.transcription.errors import ClipProblem, InvalidAudioClipError
from hivemind.llm.transcription.media import AudioMediaType
from hivemind.llm.transcription.models import (
    MAX_CLIP_BYTES,
    MAX_PCM_BYTES,
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptSegment,
)

FORMAT_MISMATCH_STATUS_CODE = 400  # "Bad Request", synthesized: one stream changed its format.
STREAM_TOO_LARGE_STATUS_CODE = 413  # "Payload Too Large", synthesized: the stream outgrew a clip.

# The one call a buffered stream makes once the speaker stops: an adapter's own `transcribe`.
Transcriber = Callable[[AudioClip, str | None], Awaitable[Transcript]]

__all__ = [
    "FORMAT_MISMATCH_STATUS_CODE",
    "STREAM_TOO_LARGE_STATUS_CODE",
    "EncodedAudioChunk",
    "Transcriber",
    "clip_from_chunks",
    "collect_clip",
    "stream_by_buffering",
]


class EncodedAudioChunk(BaseModel):
    """One frame of a device's push-to-talk recording: a slice of one clip's encoded bytes.

    Only the first frame of a stream carries the container header, so a chunk is not a playable
    clip on its own; `clip_from_chunks` joins a stream's frames into one `AudioClip`. Raw PCM
    from a Cell's own microphone travels as `AudioChunk` instead.
    """

    # Frozen, extras-forbidding, and bytes as base64 in JSON, like every audio model here.
    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )

    data: bytes = Field(
        min_length=1, max_length=MAX_CLIP_BYTES, repr=False, description="This frame's bytes."
    )
    media_type: AudioMediaType = Field(description="The whole stream's format.")
    duration_s: float | None = Field(
        default=None,
        ge=0,
        description="This frame's length in seconds when its sender knows it; None otherwise.",
    )


async def stream_by_buffering(
    transcribe: Transcriber,
    chunks: AsyncIterator[AudioChunk],
    language: str | None,
    provider: str,
) -> AsyncIterator[TranscriptSegment]:
    """Buffer `chunks` into one clip, transcribe it once, and yield the transcript's segments.

    Args:
        transcribe: The adapter's own `transcribe`, called once when the stream ends.
        chunks: The push-to-talk stream, in arrival order.
        language: The caller's ISO 639 hint, passed through unchanged.
        provider: The manifest provider name, folded into any error this raises.

    Yields:
        Every segment of the one transcript, in time order; nothing for an empty stream.

    Raises:
        ProviderRequestError: The chunks disagree on their format or outgrow one clip, or
            `transcribe` refused the assembled clip.
        RateLimitedError: `transcribe` was rate-limited.
        ProviderUnavailableError: `transcribe` could not reach or run its model.
    """
    clip = await collect_clip(chunks, provider)
    if clip is None:
        return  # Nothing was said: there is no clip to spend a transcription on.
    # External await: the adapter's own transcription (a model run or an HTTP call), bounded by
    # that adapter's own timeout; this helper adds none of its own.
    transcript = await transcribe(clip, language)
    for segment in transcript.segments:
        yield segment


async def collect_clip(chunks: AsyncIterator[AudioChunk], provider: str) -> AudioClip | None:
    """Drain `chunks` into one WAV clip, checking format and size as each chunk arrives.

    Args:
        chunks: The push-to-talk stream, in arrival order. The caller owns its pacing and its
            deadline (the speaker releasing the button, the Entrance's own idle timeout), since it
            is the caller's own producer, not an endpoint this module talks to.
        provider: The manifest provider name, folded into any error this raises.

    Returns:
        The assembled clip, or None when the stream carried no frames at all.

    Raises:
        ProviderRequestError: A chunk's sample rate or channel count differs from the first
            chunk's (400), or the frames so far exceed `MAX_PCM_BYTES` (413).
    """
    pcm = bytearray()
    first: AudioChunk | None = None
    # Each chunk either sets the stream's format (the first) or must match it; the buffer only
    # ever grows by a chunk that has already passed both checks.
    async for chunk in chunks:
        if first is None:
            first = chunk
        elif (chunk.sample_rate, chunk.channels) != (first.sample_rate, first.channels):
            raise ProviderRequestError(
                provider,
                FORMAT_MISMATCH_STATUS_CODE,
                error_type="audio_format_mismatch",
                detail=f"a chunk at {chunk.sample_rate} Hz x {chunk.channels} followed chunks at "
                f"{first.sample_rate} Hz x {first.channels}",
            )
        if len(pcm) + len(chunk.pcm) > MAX_PCM_BYTES:
            raise ProviderRequestError(
                provider,
                STREAM_TOO_LARGE_STATUS_CODE,
                error_type="clip_too_large",
                detail=f"the stream passed {MAX_PCM_BYTES} bytes of audio, more than a clip holds",
            )
        pcm.extend(chunk.pcm)
    if first is None or not pcm:
        return None
    return AudioClip.from_pcm(bytes(pcm), first.sample_rate, first.channels)


async def clip_from_chunks(chunks: AsyncIterable[EncodedAudioChunk]) -> AudioClip:
    """Join one device push-to-talk stream's encoded frames into a single clip.

    The byte ceiling is checked as frames arrive, so an over-long hold is refused without first
    buffering all of it. The clip's duration is the sum of the frames' when every frame gave one
    (a WAV stream's header decides regardless).

    Args:
        chunks: The stream's frames, in order. The caller's iterator owns its own pacing and
            timeout (the Entrance bounds how long a push-to-talk hold may last).

    Returns:
        The whole recording as one validated AudioClip.

    Raises:
        InvalidAudioClipError: The stream is empty or changes format mid-way (`MALFORMED`),
            exceeds `MAX_CLIP_BYTES` (`TOO_LARGE`), or fails any `AudioClip.from_upload` rule.
    """
    parts: list[bytes] = []
    total_bytes = 0
    total_s: float | None = 0.0
    format_: AudioMediaType | None = None
    # Accumulate every frame's bytes and, while every frame reports one, its duration.
    async for chunk in chunks:
        format_ = format_ if format_ is not None else chunk.media_type
        if chunk.media_type is not format_:
            raise InvalidAudioClipError(ClipProblem.MALFORMED, "a stream changed format mid-way")
        total_bytes += len(chunk.data)
        if total_bytes > MAX_CLIP_BYTES:
            raise InvalidAudioClipError(
                ClipProblem.TOO_LARGE, f"the stream passed the {MAX_CLIP_BYTES} byte limit"
            )
        parts.append(chunk.data)
        total_s = (
            None if total_s is None or chunk.duration_s is None else total_s + chunk.duration_s
        )
    if format_ is None:
        raise InvalidAudioClipError(ClipProblem.MALFORMED, "the stream carried no audio")
    return AudioClip.from_upload(b"".join(parts), format_, total_s)
