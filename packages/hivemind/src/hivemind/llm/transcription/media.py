"""Define AudioMediaType: the closed set of audio formats the Hive accepts for transcription.

A clip arrives labelled with whatever its sender calls it: a browser's recorder says
`audio/webm;codecs=opus` or `audio/ogg;codecs=opus` or `audio/mp4`, a program says `audio/x-wav`,
another says `audio/mp3`. `AudioMediaType.parse` folds every accepted spelling onto one of five
members -- WAV, Ogg/Opus, WebM/Opus, MP3 and M4A, the formats every Whisper-style server decodes
-- and refuses the rest before any bytes travel further. Each member also knows the file extension
a transcription server expects on an upload's filename, since an OpenAI-compatible server picks
its decoder from that extension rather than from the part's content type.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Read by
    `hivemind.llm.transcription.models` (an `AudioClip` carries one), by every
    `TranscriptionProvider` (its capabilities list the members it accepts) and by the
    openai_compat adapter (for the upload's filename). Imports only this package's errors.

Key invariants:
    - A member's value is the canonical media type sent on the wire for that format.
    - An Ogg or WebM label naming a codec other than Opus is refused: only Opus-in-Ogg and
      Opus-in-WebM are accepted formats, and a label that says otherwise is not guessed at.
    - `parse` is case-insensitive and ignores every parameter except `codecs`.

See Also:
    - .claude/codingrules.md section 8.15, "Voice is transcribed at the door", for where clips
      come from.
    - hivemind.llm.transcription.models for AudioClip, which carries a member of this enum.
"""

from __future__ import annotations

from enum import Enum

from hivemind.llm.transcription.errors import ClipProblem, InvalidAudioClipError

_CODECS_KEY = "codecs"  # The one media type parameter this module reads.
_OPUS_CODEC = "opus"  # The only codec an Ogg or WebM clip may declare.

__all__ = ["AudioMediaType"]


class AudioMediaType(Enum):
    """One accepted audio format, valued by its canonical media type."""

    WAV = "audio/wav"  # Uncompressed PCM (or float) in a RIFF container; its header has a length.
    OGG_OPUS = "audio/ogg"  # Opus in Ogg: Firefox's recorder default.
    WEBM_OPUS = "audio/webm"  # Opus in WebM: Chrome's and Android's recorder default.
    MP3 = "audio/mpeg"  # MPEG-1 Layer III: the lowest common denominator for programs.
    M4A = "audio/mp4"  # AAC in an MP4 container: Safari's and iOS's recorder default.

    @property
    def extension(self) -> str:
        """Return the filename extension a transcription server expects for this format."""
        return _EXTENSIONS[self]

    @classmethod
    def parse(cls, raw: str) -> AudioMediaType:
        """Return the format a Content-Type-style label names.

        Args:
            raw: A media type with optional parameters, e.g. `"audio/webm;codecs=opus"`.

        Returns:
            The accepted format the label names.

        Raises:
            InvalidAudioClipError: `UNSUPPORTED_FORMAT` when the label names no accepted format,
                or names Ogg or WebM with a codec other than Opus.
        """
        base, _, parameters = raw.partition(";")
        member = _ALIASES.get(base.strip().lower())
        if member is None:
            accepted = ", ".join(format_.value for format_ in cls)
            raise InvalidAudioClipError(
                ClipProblem.UNSUPPORTED_FORMAT,
                f"media type {base.strip()!r} is not one of the accepted formats ({accepted})",
            )
        codec = _codec(parameters)
        # Only the two Opus containers constrain their codec; an MP4 or MP3 label's codecs
        # parameter is informational, since each of those formats already implies its codec.
        if member in _OPUS_CONTAINERS and codec is not None and codec != _OPUS_CODEC:
            raise InvalidAudioClipError(
                ClipProblem.UNSUPPORTED_FORMAT,
                f"{member.value} is accepted with the opus codec only, not {codec!r}",
            )
        return member


def _codec(parameters: str) -> str | None:
    """Return the lower-cased `codecs` parameter from a media type's parameter list, if any.

    Args:
        parameters: Everything after the first `;`, e.g. `' codecs="opus"; rate=48000'`.
    """
    for parameter in parameters.split(";"):
        key, _, value = parameter.partition("=")
        if key.strip().lower() == _CODECS_KEY:
            return value.strip().strip('"').lower()
    return None


# The tables below are keyed by the enum's own members, so they can only follow its definition;
# they are read at call time, never at import, so their position costs nothing.
_OPUS_CONTAINERS = frozenset({AudioMediaType.OGG_OPUS, AudioMediaType.WEBM_OPUS})  # Opus-only.

# Every spelling senders are known to use, folded onto its member. `audio/opus` is what some
# tools label an .opus file, which is Opus in Ogg.
_ALIASES: dict[str, AudioMediaType] = {
    "audio/wav": AudioMediaType.WAV,
    "audio/x-wav": AudioMediaType.WAV,
    "audio/wave": AudioMediaType.WAV,
    "audio/vnd.wave": AudioMediaType.WAV,
    "audio/ogg": AudioMediaType.OGG_OPUS,
    "audio/opus": AudioMediaType.OGG_OPUS,
    "audio/webm": AudioMediaType.WEBM_OPUS,
    "audio/mpeg": AudioMediaType.MP3,
    "audio/mp3": AudioMediaType.MP3,
    "audio/mp4": AudioMediaType.M4A,
    "audio/m4a": AudioMediaType.M4A,
    "audio/x-m4a": AudioMediaType.M4A,
}

# The extension an OpenAI-compatible server reads to choose its decoder.
_EXTENSIONS: dict[AudioMediaType, str] = {
    AudioMediaType.WAV: "wav",
    AudioMediaType.OGG_OPUS: "ogg",
    AudioMediaType.WEBM_OPUS: "webm",
    AudioMediaType.MP3: "mp3",
    AudioMediaType.M4A: "m4a",
}
