"""Define the transcription boundary's own value family: audio in, time-stamped transcripts out.

Transcription is speech in, text out, on `ModelSlot.TRANSCRIBER` (the one model slot that hears).
Codingrules section 8.6 keeps every vendor and library type inside its adapter, so the values that
cross this boundary are HiveMind's own frozen models (ADR-0033): an `AudioClip` goes in (a whole
WAV file plus the facts its header states, checked against that header so a caller can never
under-report how long a clip is; or, since roadmap step 10.5f, a device's compressed recording
-- Opus in Ogg or WebM, MP3, M4A -- whose length its sender states, built with `from_upload`), an
`AudioChunk` is one piece of a push-to-talk stream (raw PCM frames, assembled into a clip by
`hivemind.llm.transcription.buffered`), and a `Transcript` of time-stamped `TranscriptSegment`s
comes back. Audio is personal data (`C2` when it came from a
Real Cell or the human) and transient: it lives in memory for the call only, so neither audio
model ever puts its bytes in a `repr`, and none of these models is ever written to the Pheromone
Trail (the Hive's append-only audit log).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Built by callers (a Worker's `listen` tool through Buzz, the Exoskeleton's audio; the Hive
    Entrance's voice route, roadmap step 10.5f) and by every transcription adapter's own mapping;
    read by `hivemind.llm.transcription.capabilities` (the limits check) and the Fanner (the seat
    meter every model call passes through), which meters `AudioClip.duration_s`. Calls into
    `hivemind.llm.transcription.errors`, `.media`, `.wav` and pydantic only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5); bytes
      travel as base64 in JSON so a clip survives a JSON round trip unchanged.
    - A WAV `AudioClip`'s `sample_rate`, `channels` and `duration_s` agree with its own header,
      to within `DURATION_TOLERANCE_S`; `from_wav`, `from_pcm` and `from_upload` derive them so
      they always do. A compressed clip states no sample rate or channels (they are not known
      without decoding it), and `from_upload` refuses one with no positive duration.
    - `from_upload` reports every refusal as an `InvalidAudioClipError` with a `ClipProblem`,
      never a pydantic error, so the Entrance's voice route answers without parsing a message.
    - `AudioClip.data` and `AudioChunk.pcm` are excluded from `repr`: audio never reaches a log
      line by way of an f-string (codingrules section 12).
    - A `TranscriptSegment` never ends before it starts, and a `Transcript`'s segments are in
      time order (`Transcript.from_segments` sorts them, so an adapter never has to).

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the decision these models serve.
    - hivemind.llm.transcription.provider for TranscriptionProvider, the Protocol they cross.
    - hivemind.llm.transcription.wav for the WAV header rules AudioClip is checked against.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hivemind.llm.transcription.errors import ClipProblem, InvalidAudioClipError
from hivemind.llm.transcription.media import AudioMediaType
from hivemind.llm.transcription.wav import (
    PCM_SAMPLE_WIDTH_BYTES,
    WAV_HEADER_BYTES,
    encode_wav,
    read_wav_header,
)

MAX_CLIP_BYTES = 25 * 1024 * 1024  # 25 MiB: the common hosted /audio/transcriptions upload cap,
# and a bound on the audio one call holds in memory (about 13 minutes of 16 kHz mono 16-bit PCM).
MAX_PCM_BYTES = MAX_CLIP_BYTES - WAV_HEADER_BYTES  # The frames that still fit once wrapped as WAV.
MAX_SAMPLE_RATE = 192_000  # The highest rate consumer recording hardware offers.
MAX_CHANNELS = 2  # Mono or stereo: a microphone or a desktop's mixed output, never surround.
DURATION_TOLERANCE_S = 0.001  # A declared duration may differ from the header's by one millisecond.
LANGUAGE_PATTERN = r"^[a-z]{2,3}$"  # An ISO 639-1 code ("en"), or 639-3 where Whisper uses one.
MAX_TRANSCRIPT_CHARS = 100_000  # Far above what the longest clip can hold spoken (about 20
# characters a second); only a runaway reply ever reaches it.
MAX_CLIP_SECONDS = 600.0  # Ten minutes: the longest upload `from_upload` accepts from a device.

# The one container whose facts the standard library reads without a codec (see `wav`); every
# other AudioMediaType is a compressed recording whose length its sender states.
WAV_MEDIA_TYPE = AudioMediaType.WAV

__all__ = [
    "DURATION_TOLERANCE_S",
    "LANGUAGE_PATTERN",
    "MAX_CHANNELS",
    "MAX_CLIP_BYTES",
    "MAX_CLIP_SECONDS",
    "MAX_PCM_BYTES",
    "MAX_SAMPLE_RATE",
    "MAX_TRANSCRIPT_CHARS",
    "WAV_MEDIA_TYPE",
    "AudioChunk",
    "AudioClip",
    "Transcript",
    "TranscriptSegment",
]


class AudioClip(BaseModel):
    """One whole recording to transcribe: a WAV file and what its header says, or an upload.

    Crosses the transcription boundary inward (`TranscriptionProvider.transcribe`). Build one with
    `from_wav`, `from_pcm` or (a device's upload, in any `AudioMediaType`) `from_upload` rather
    than field by field, so the stated facts are measured, not typed in; the validator still
    checks a hand-built WAV clip against its own header.
    """

    # Frozen, extras-forbidding, and bytes as base64 in JSON: raw audio is not UTF-8, so
    # pydantic's default bytes-as-text JSON encoding would fail on the first clip it met.
    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )

    data: bytes = Field(
        max_length=MAX_CLIP_BYTES,
        repr=False,
        description="The whole recording, container header included. Never logged, never on "
        "the trail.",
    )
    media_type: AudioMediaType = Field(
        default=WAV_MEDIA_TYPE, description="The container and codec `data` is in."
    )
    sample_rate: int | None = Field(
        default=None,
        gt=0,
        le=MAX_SAMPLE_RATE,
        description="Frames per second, as the WAV header states; None for a compressed clip.",
    )
    channels: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CHANNELS,
        description="Channels per frame, as the WAV header states; None for a compressed clip.",
    )
    duration_s: float = Field(
        ge=0, description="How long the audio lasts, in seconds, measured from its frames."
    )

    @model_validator(mode="after")
    def _facts_match_the_header(self) -> AudioClip:
        """Reject a clip whose stated sample rate, channels or duration its header contradicts."""
        if self.media_type is not AudioMediaType.WAV:
            # A compressed clip's header needs a codec to read; its sender's length stands, and
            # no sample rate or channel count can be stated for it at all.
            if self.sample_rate is not None or self.channels is not None:
                raise ValueError(
                    f"a {self.media_type.value} clip states no sample rate or channels."
                )
            return self
        header = read_wav_header(self.data)
        # The Fanner meters audio seconds from duration_s, so a clip that under-reports its own
        # length would be under-billed; the header, not the caller, is the source of truth.
        if (header.sample_rate, header.channels) != (self.sample_rate, self.channels):
            raise ValueError(
                f"clip states {self.sample_rate} Hz x {self.channels} channel(s) but its WAV "
                f"header says {header.sample_rate} Hz x {header.channels}."
            )
        if abs(header.duration_s - self.duration_s) > DURATION_TOLERANCE_S:
            raise ValueError(
                f"clip states {self.duration_s:.3f}s but its frames last {header.duration_s:.3f}s."
            )
        return self

    @classmethod
    def from_wav(cls, data: bytes) -> AudioClip:
        """Build a clip from a whole WAV file, reading every stated fact from its own header.

        Args:
            data: A complete PCM WAV file of at most `MAX_CLIP_BYTES`.

        Returns:
            A validated AudioClip.

        Raises:
            ValueError: `data` is not a readable PCM WAV file (pydantic's `ValidationError`, a
                `ValueError`, when it is readable but breaks a bound above).
        """
        header = read_wav_header(data)
        return cls(
            data=data,
            sample_rate=header.sample_rate,
            channels=header.channels,
            duration_s=header.duration_s,
        )

    @classmethod
    def from_pcm(cls, pcm: bytes, sample_rate: int, channels: int) -> AudioClip:
        """Build a clip from raw 16-bit PCM frames, wrapping them in a WAV header first.

        Args:
            pcm: Interleaved 16-bit little-endian samples, whole frames only.
            sample_rate: Frames per second. Must be > 0.
            channels: Channels per frame. Must be 1 or 2.

        Returns:
            A validated AudioClip over the encoded WAV file.

        Raises:
            ValueError: The frames cannot be wrapped, or the result breaks a bound above.
        """
        return cls.from_wav(encode_wav(pcm, sample_rate, channels))

    @classmethod
    def from_upload(
        cls, data: bytes, media_type: str | AudioMediaType, duration_s: float | None = None
    ) -> AudioClip:
        """Build a clip from what a device sent (roadmap step 10.5f), checking every ceiling.

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
        if format_ is not AudioMediaType.WAV:
            seconds = _checked_duration(format_, duration_s)
            return _build(lambda: cls(data=data, media_type=format_, duration_s=seconds))
        try:
            header = read_wav_header(data)
        except ValueError as exc:
            raise InvalidAudioClipError(ClipProblem.MALFORMED, str(exc)) from exc
        _checked_duration(format_, header.duration_s)
        return _build(lambda: cls.from_wav(data))


class AudioChunk(BaseModel):
    """One piece of a push-to-talk stream: raw PCM frames as they arrive from a microphone.

    Crosses the transcription boundary inward (`TranscriptionProvider.stream`). Raw frames rather
    than WAV pieces because a stream has no length to put in a header until it ends; every chunk
    of one stream shares one sample rate and channel count.
    """

    # Frozen, extras-forbidding, and bytes as base64 in JSON: raw audio is not UTF-8, so
    # pydantic's default bytes-as-text JSON encoding would fail on the first clip it met.
    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )

    pcm: bytes = Field(
        max_length=MAX_PCM_BYTES,
        repr=False,
        description="Interleaved 16-bit little-endian samples, whole frames only. Never logged.",
    )
    sample_rate: int = Field(gt=0, le=MAX_SAMPLE_RATE, description="Frames per second.")
    channels: int = Field(ge=1, le=MAX_CHANNELS, description="Channels per frame.")

    @model_validator(mode="after")
    def _whole_frames_only(self) -> AudioChunk:
        """Reject a chunk that ends partway through a frame; the next chunk could not realign."""
        frame_bytes = PCM_SAMPLE_WIDTH_BYTES * self.channels
        if len(self.pcm) % frame_bytes:
            raise ValueError(
                f"chunk holds {len(self.pcm)} bytes, not a whole number of "
                f"{frame_bytes}-byte frames."
            )
        return self


class TranscriptSegment(BaseModel):
    """One time-stamped phrase of a transcript: when it was said, what was said, how sure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_s: float = Field(ge=0, description="Seconds from the clip's start to the phrase's.")
    end_s: float = Field(ge=0, description="Seconds from the clip's start to the phrase's end.")
    text: str = Field(max_length=MAX_TRANSCRIPT_CHARS, description="What was said, trimmed.")
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="How likely the model thinks this text is, from 0 to 1; None when the "
        "provider reported no figure.",
    )

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> TranscriptSegment:
        """Reject a segment whose end precedes its start."""
        if self.end_s < self.start_s:
            raise ValueError(f"segment ends at {self.end_s}s, before it starts at {self.start_s}s.")
        return self


class Transcript(BaseModel):
    """What one clip said: the full text, its language, and its time-stamped segments.

    Crosses the transcription boundary outward. Carries the clearance of its source when stored
    (`C2` for anything from a Real Cell or the human, ADR-0033); nothing here is ever written to
    the Pheromone Trail.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(max_length=MAX_TRANSCRIPT_CHARS, description="The whole transcript.")
    language: str | None = Field(
        default=None,
        pattern=LANGUAGE_PATTERN,
        description="The language spoken, as a lowercase ISO 639 code; None when unknown.",
    )
    duration_s: float = Field(ge=0, description="How long the transcribed clip lasts, in seconds.")
    segments: tuple[TranscriptSegment, ...] = Field(
        default=(), description="Time-stamped phrases, in time order; empty for silence."
    )

    @model_validator(mode="after")
    def _segments_in_time_order(self) -> Transcript:
        """Reject segments that are not ordered by their start time."""
        starts = [segment.start_s for segment in self.segments]
        if starts != sorted(starts):
            raise ValueError("transcript segments must be in time order.")
        return self

    @classmethod
    def from_segments(
        cls,
        segments: Iterable[TranscriptSegment],
        *,
        language: str | None,
        duration_s: float,
    ) -> Transcript:
        """Build a transcript whose text is its segments' text, joined in time order.

        Args:
            segments: The phrases, in any order; sorted by start time here.
            language: The spoken language's ISO 639 code, or None when unknown.
            duration_s: The transcribed clip's own duration, in seconds.

        Returns:
            A validated Transcript; its `text` joins every non-empty segment's text with one
            space, and is empty when there are no segments (silence).
        """
        ordered = tuple(sorted(segments, key=lambda segment: segment.start_s))
        text = " ".join(segment.text for segment in ordered if segment.text)
        return cls(text=text, language=language, duration_s=duration_s, segments=ordered)


def _checked_duration(format_: AudioMediaType, seconds: float | None) -> float:
    """Return an upload's duration once it is known, positive, finite and within the ceiling.

    Raises:
        InvalidAudioClipError: No duration for a compressed clip (`MISSING_DURATION`), one that
            is not a positive finite number (`MALFORMED`), or one past `MAX_CLIP_SECONDS`
            (`TOO_LONG`).
    """
    if seconds is None:
        raise InvalidAudioClipError(
            ClipProblem.MISSING_DURATION,
            f"a {format_.value} clip needs its duration from its sender",
        )
    if not math.isfinite(seconds) or seconds <= 0:
        raise InvalidAudioClipError(ClipProblem.MALFORMED, f"duration {seconds!r} is not positive")
    if seconds > MAX_CLIP_SECONDS:
        raise InvalidAudioClipError(
            ClipProblem.TOO_LONG, f"{seconds:.1f} s exceeds the {MAX_CLIP_SECONDS:.0f} s limit"
        )
    return seconds


def _build(make: Callable[[], AudioClip]) -> AudioClip:
    """Run `make`, turning a model validation failure into a MALFORMED clip refusal."""
    try:
        return make()
    except ValidationError as exc:
        # Every named rule has passed, so what is left is a malformed value those checks could
        # not name more precisely; chained so the trail keeps pydantic's own detail.
        raise InvalidAudioClipError(ClipProblem.MALFORMED, "the clip failed validation") from exc
