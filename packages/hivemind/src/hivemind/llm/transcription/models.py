"""Define the transcription boundary's own models: AudioClip and AudioChunk in, Transcript out.

Every transcription in the Hive -- the human's voice at the Entrance (the one door into the
Hive), a Worker's ears through Buzz (the Exoskeleton's audio) later -- crosses this boundary as
HiveMind's own values, never a library's: an `AudioClip` (the bytes, one accepted
`AudioMediaType`, and a duration in seconds) goes in, a `Transcript` (text, language, duration
and timestamped `TranscriptSegment`s) comes out. The input is bounded here, once, for every
provider: `MAX_CLIP_BYTES` and `MAX_CLIP_SECONDS` are ceilings no clip passes whoever sent it (the
Entrance enforces the operator's tighter `[entrance.voice] max_clip_seconds` on top).
`AudioClip.from_upload` is how a caller builds one from what a device sent: it names the format,
checks both ceilings and fills in the duration -- read from the header for a WAV, taken from the
sender for a compressed format, since no library that could decode one is a dependency.
`AudioChunk` and `clip_from_chunks` serve push-to-talk: the frames of one recording, gathered
into one clip.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Built by the Entrance's
    voice route (roadmap step 10.5f) and read by every `TranscriptionProvider`; a `Transcript` is
    what the Entrance turns into a `HumanMessage` for the Queen's inbox. Imports this package's
    `media`, `wav` and `errors` modules and pydantic only.

Key invariants:
    - Every model is frozen and forbids unknown fields (codingrules section 8.5).
    - A WAV clip's `duration_s` always equals what its own header declares: the model refuses a
      mismatch, so a sender can never under-report a WAV's length. A compressed clip's duration
      is its sender's claim; `MAX_CLIP_BYTES` bounds what a false claim can cost, and a
      provider's own measured duration comes back on `Transcript.duration_s`.
    - Audio bytes and transcript text are C2 (the human's own words) and never appear in a
      `repr`, so an exception or a log line that formats a model cannot leak them
      (codingrules section 12).
    - Bytes cross JSON as base64 (`ser_json_bytes`/`val_json_bytes`), so a clip round-trips.

See Also:
    - .claude/codingrules.md section 8.15, "Voice is transcribed at the door".
    - .claude/roadmap.md steps 6.5a and 10.5f for the provider and its first caller.
    - hivemind.llm.transcription.provider for the TranscriptionProvider these models cross.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hivemind.llm.transcription.errors import ClipProblem, InvalidAudioClipError
from hivemind.llm.transcription.media import AudioMediaType
from hivemind.llm.transcription.wav import wav_duration_s

MAX_CLIP_BYTES = 25_000_000  # The hosted speech-to-text APIs' own 25 MB upload cap: one ceiling
# for every provider, so a clip a local server accepts is one a hosted fallback accepts too.
MAX_CLIP_SECONDS = 600.0  # Ten minutes: five times [entrance.voice] max_clip_seconds's default;
# the operator's own cap is enforced by the Entrance, this is the floor under every caller.
MAX_TRANSCRIPT_CHARS = 100_000  # Ten minutes of fast speech is under 15,000 characters.
MAX_TRANSCRIPT_SEGMENTS = 2_000  # A segment every 0.3 s for ten minutes; real servers emit far
# fewer (a few seconds each).
MAX_LANGUAGE_CHARS = 32  # "en" from most servers, "english" from some; never a sentence.
_DURATION_TOLERANCE_S = 1e-6  # Float noise allowed between a WAV's stated and header duration.
_MAX_LANGUAGE_SUBTAG_CHARS = 3  # ISO 639-1 codes are two letters, ISO 639-2/3 three.
_MIN_LANGUAGE_SUBTAG_CHARS = 2

__all__ = [
    "MAX_CLIP_BYTES",
    "MAX_CLIP_SECONDS",
    "MAX_LANGUAGE_CHARS",
    "MAX_TRANSCRIPT_CHARS",
    "MAX_TRANSCRIPT_SEGMENTS",
    "AudioChunk",
    "AudioClip",
    "Transcript",
    "TranscriptSegment",
    "clip_from_chunks",
    "normalise_language",
]

# Frozen and closed like every boundary model; bytes travel as base64 in JSON so a clip survives
# a round trip (the default would try to read arbitrary audio as UTF-8).
_BYTES_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
)
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class AudioClip(BaseModel):
    """One recording to transcribe: its bytes, its format and its length.

    Crosses from the Entrance (or Buzz) into a `TranscriptionProvider`; build one from a device's
    upload with `from_upload`, which reports every refusal as an `InvalidAudioClipError`.
    """

    model_config = _BYTES_MODEL_CONFIG

    data: bytes = Field(
        min_length=1,
        max_length=MAX_CLIP_BYTES,
        repr=False,
        description="The encoded audio, container header included; C2, never logged.",
    )
    media_type: AudioMediaType = Field(description="The clip's format.")
    duration_s: float = Field(
        gt=0,
        le=MAX_CLIP_SECONDS,
        description="The clip's length in seconds: its header's for WAV, its sender's otherwise.",
    )

    @model_validator(mode="after")
    def _wav_duration_matches_its_header(self) -> AudioClip:
        """Refuse a WAV clip whose stated duration is not the one its own header declares."""
        if self.media_type is not AudioMediaType.WAV:
            return self  # A compressed clip's length cannot be read without decoding it.
        header_s = wav_duration_s(self.data)
        if not math.isclose(header_s, self.duration_s, abs_tol=_DURATION_TOLERANCE_S):
            raise ValueError(
                f"a WAV clip's duration_s ({self.duration_s}) must match its header "
                f"({header_s}); build it with AudioClip.from_upload to read the header"
            )
        return self

    @classmethod
    def from_upload(
        cls, data: bytes, media_type: str | AudioMediaType, duration_s: float | None = None
    ) -> AudioClip:
        """Build a clip from what a device sent, checking every ceiling.

        Args:
            data: The encoded audio as received.
            media_type: The sender's label (`"audio/webm;codecs=opus"`) or an already-parsed
                format.
            duration_s: The sender's stated length in seconds. Required for a compressed
                format; ignored for WAV, whose header is authoritative.

        Returns:
            A validated AudioClip.

        Raises:
            InvalidAudioClipError: The format is not accepted (`UNSUPPORTED_FORMAT`), the clip
                is too big (`TOO_LARGE`) or too long (`TOO_LONG`), a compressed clip came with
                no duration (`MISSING_DURATION`), or it is empty or unreadable (`MALFORMED`).
        """
        format_ = (
            media_type
            if isinstance(media_type, AudioMediaType)
            else AudioMediaType.parse(media_type)
        )
        # Size first: it is the cheapest check and bounds every later one's work.
        if not data:
            raise InvalidAudioClipError(ClipProblem.MALFORMED, "the clip is empty")
        if len(data) > MAX_CLIP_BYTES:
            raise InvalidAudioClipError(
                ClipProblem.TOO_LARGE, f"{len(data)} bytes exceeds the {MAX_CLIP_BYTES} limit"
            )
        seconds = _duration_for(format_, data, duration_s)
        try:
            return cls(data=data, media_type=format_, duration_s=seconds)
        except ValidationError as exc:
            # Every rule above has passed, so what is left is a malformed value the checks could
            # not name more precisely; chained so the trail keeps pydantic's own detail.
            raise InvalidAudioClipError(
                ClipProblem.MALFORMED, "the clip failed validation"
            ) from exc


class AudioChunk(BaseModel):
    """One frame of a push-to-talk recording: a slice of one clip's bytes, in arrival order.

    Only the first frame of a stream carries the container header, so a chunk is not a playable
    clip on its own; `clip_from_chunks` joins a stream's frames into one `AudioClip`.
    """

    model_config = _BYTES_MODEL_CONFIG

    data: bytes = Field(
        min_length=1, max_length=MAX_CLIP_BYTES, repr=False, description="This frame's bytes."
    )
    media_type: AudioMediaType = Field(description="The whole stream's format.")
    duration_s: float | None = Field(
        default=None,
        ge=0,
        description="This frame's length in seconds when its sender knows it; None otherwise.",
    )


class TranscriptSegment(BaseModel):
    """One timestamped stretch of a transcript, as the provider split it."""

    model_config = _MODEL_CONFIG

    start_s: float = Field(ge=0, description="Where this segment starts, seconds into the clip.")
    end_s: float = Field(ge=0, description="Where it ends, seconds into the clip.")
    text: str = Field(
        max_length=MAX_TRANSCRIPT_CHARS, repr=False, description="What was said; C2, never logged."
    )

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> TranscriptSegment:
        """Refuse a segment that ends before it starts."""
        if self.end_s < self.start_s:
            raise ValueError(f"a segment cannot end ({self.end_s}) before it starts")
        return self


class Transcript(BaseModel):
    """What a provider heard in one clip: text, language, length and timestamped segments.

    Crosses from a `TranscriptionProvider` back to its caller; the Entrance turns `text` into a
    `HumanMessage`. `segments` is empty when the provider reports none (its capabilities say so).
    """

    model_config = _MODEL_CONFIG

    text: str = Field(
        max_length=MAX_TRANSCRIPT_CHARS, repr=False, description="The whole transcript; C2."
    )
    language: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_LANGUAGE_CHARS,
        description="The language spoken, as the provider named it; None when unknown.",
    )
    duration_s: float = Field(
        ge=0,
        description="The audio's length in seconds: the provider's own measurement when it "
        "reports one, otherwise the clip's.",
    )
    segments: tuple[TranscriptSegment, ...] = Field(
        default=(),
        max_length=MAX_TRANSCRIPT_SEGMENTS,
        description="Timestamped segments in order; empty when the provider reports none.",
    )


async def clip_from_chunks(chunks: AsyncIterable[AudioChunk]) -> AudioClip:
    """Join one push-to-talk stream's frames into a single clip.

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


def normalise_language(language: str | None) -> str | None:
    """Reduce a language hint to the primary subtag a transcription server expects.

    A device's locale arrives as `"en-US"` or `"EN"`; servers take `"en"`. A hint is advisory, so
    one that is not a plausible language code is dropped (the provider then detects the language
    itself) rather than refused.

    Args:
        language: A BCP 47 tag, an ISO 639 code, or None.

    Returns:
        The lower-cased primary subtag when it is two or three ASCII letters; None otherwise.

    Example:
        >>> normalise_language("en-US")
        'en'
    """
    if language is None:
        return None
    primary = language.strip().replace("_", "-").partition("-")[0].lower()
    fits = _MIN_LANGUAGE_SUBTAG_CHARS <= len(primary) <= _MAX_LANGUAGE_SUBTAG_CHARS
    return primary if fits and primary.isascii() and primary.isalpha() else None


def _duration_for(format_: AudioMediaType, data: bytes, claimed_s: float | None) -> float:
    """Return a clip's duration: read from a WAV header, else the sender's checked claim.

    Raises:
        InvalidAudioClipError: A WAV header that does not parse or declares no audio
            (`MALFORMED`), a missing claim for a compressed clip (`MISSING_DURATION`), a claim
            that is not a positive finite number (`MALFORMED`), or one past the ceiling
            (`TOO_LONG`).
    """
    if format_ is AudioMediaType.WAV:
        try:
            seconds = wav_duration_s(data)
        except ValueError as exc:
            raise InvalidAudioClipError(ClipProblem.MALFORMED, str(exc)) from exc
    elif claimed_s is None:
        raise InvalidAudioClipError(
            ClipProblem.MISSING_DURATION,
            f"a {format_.value} clip needs its duration from its sender",
        )
    else:
        seconds = claimed_s
    if not math.isfinite(seconds) or seconds <= 0:
        raise InvalidAudioClipError(ClipProblem.MALFORMED, f"duration {seconds!r} is not positive")
    if seconds > MAX_CLIP_SECONDS:
        raise InvalidAudioClipError(
            ClipProblem.TOO_LONG, f"{seconds:.1f} s exceeds the {MAX_CLIP_SECONDS:.0f} s limit"
        )
    return seconds
