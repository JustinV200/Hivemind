"""Define the transcription boundary's own refusal: a clip no provider should see.

A clip a device uploads can be unusable before any audio reaches a model (too many bytes, too
many seconds, a format the Hive does not accept, a compressed clip whose sender gave no duration,
or a WAV whose header does not parse): `InvalidAudioClipError`, raised while the clip is being
built (`AudioClip.from_upload`, `clip_from_chunks`), with a `ClipProblem` a caller such as the
Entrance's voice route maps to its own response (too large, unsupported, bad request) without
parsing a message. A `TRANSCRIBER` slot bound to a kind that cannot transcribe is the registry's
own `hivemind.llm.registry.TranscriptionUnsupportedError`, raised when the slot is bound.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Raised by
    `hivemind.llm.transcription.models` (building a clip), `.buffered` (joining a stream's
    frames) and `.media` (naming a format). Imports `hivemind.common.errors` only.

Key invariants:
    - `InvalidAudioClipError` is deliberately not an `LLMError`: it carries no provider, because
      no provider was involved, and a caller that catches `LLMError` to try a fallback binding
      must not catch it -- no fallback accepts a clip the Hive itself refuses.
    - Messages name sizes, durations and formats; never audio bytes or transcript text
      (codingrules section 12).

See Also:
    - .claude/codingrules.md section 10 for the error rules this module follows.
    - hivemind.llm.errors for LLMError and the adapter errors a transcription call can raise.
    - hivemind.llm.transcription.models for AudioClip.from_upload, the main raiser.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import HiveMindError

__all__ = ["ClipProblem", "InvalidAudioClipError"]


class ClipProblem(Enum):
    """Why an audio clip was refused before any provider saw it."""

    TOO_LARGE = "TOO_LARGE"  # More bytes than the clip byte ceiling.
    TOO_LONG = "TOO_LONG"  # More seconds than the clip duration ceiling.
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"  # A media type outside the accepted formats.
    MISSING_DURATION = "MISSING_DURATION"  # A compressed clip whose sender gave no duration.
    MALFORMED = "MALFORMED"  # Empty, a WAV header that does not parse, or a broken stream.


class InvalidAudioClipError(HiveMindError):
    """Raise when an audio clip cannot be accepted for transcription at all."""

    code: ClassVar[str] = "hivemind.llm.invalid_audio_clip"

    def __init__(self, problem: ClipProblem, detail: str) -> None:
        """Build the error for one refused clip.

        Args:
            problem: Which rule the clip broke; what a caller branches on.
            detail: A short sentence fragment naming the sizes or format involved, never any of
                the audio itself.
        """
        super().__init__(f"Audio clip refused ({problem.value}): {detail}.")
        self.problem = problem
