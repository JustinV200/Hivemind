"""Define the transcription boundary's own refusals: a clip no provider should see, and a binding.

Two things can go wrong before any audio reaches a model. The clip itself can be unusable (too
many bytes, too many seconds, a format the Hive does not accept, a compressed clip whose sender
gave no duration, or a WAV whose header does not parse): `InvalidAudioClipError`, raised while
the clip is being built, with a `ClipProblem` a caller such as the Entrance's voice route maps to
its own response (too large, unsupported, bad request) without parsing a message. Or the
`TRANSCRIBER` slot can be bound to a provider whose kind has no transcription adapter at all:
`TranscriptionUnsupportedError`, raised when the slot is bound, never at the first call.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Raised by
    `hivemind.llm.transcription.models` (building a clip), `hivemind.llm.transcription.media`
    (naming a format) and `hivemind.llm.registry.ProviderRegistry.transcriber` (binding the
    slot). Imports `hivemind.common.errors` and `hivemind.llm.errors` only.

Key invariants:
    - `InvalidAudioClipError` is deliberately not an `LLMError`: it carries no provider, because
      no provider was involved, and a caller that catches `LLMError` to try a fallback binding
      must not catch it -- no fallback accepts a clip the Hive itself refuses.
    - `TranscriptionUnsupportedError` is an `LLMError` naming the provider, its kind and the
      manifest row, so the operator can fix `[llm.slots]` from the message alone.
    - Messages name sizes, durations, formats and manifest keys; never audio bytes or transcript
      text (codingrules section 12).

See Also:
    - .claude/codingrules.md section 10 for the error rules this module follows.
    - hivemind.llm.errors for LLMError and the adapter errors a transcription call can raise.
    - hivemind.llm.transcription.models for AudioClip.from_upload, the main raiser.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import ClassVar

from hivemind.common.errors import HiveMindError
from hivemind.llm.errors import LLMError

__all__ = ["ClipProblem", "InvalidAudioClipError", "TranscriptionUnsupportedError"]


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


class TranscriptionUnsupportedError(LLMError):
    """Raise when the TRANSCRIBER slot is bound to a provider kind that cannot transcribe."""

    code: ClassVar[str] = "hivemind.llm.transcription_unsupported"

    def __init__(self, provider: str, kind: str, binding: str, supported: Iterable[str]) -> None:
        """Build the error for one unusable `[llm.slots]` row.

        Args:
            provider: The `[llm.providers.<name>]` key the row names.
            kind: That provider's kind, which has no transcription adapter.
            binding: The `[llm.slots]` key of the row: the slot's own or a fallback's.
            supported: Every kind that can transcribe, for the message's suggested fix.
        """
        kinds = ", ".join(sorted(supported))
        super().__init__(
            f"[llm.slots.{binding}] binds provider {provider!r} of kind {kind!r}, which cannot "
            f"transcribe audio; bind it to a provider of kind {kinds}.",
            provider=provider,
        )
        self.kind = kind
        self.binding = binding
