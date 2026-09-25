"""Define TranscriptionCapabilities: what one transcriber can hear, checked before it runs.

Codingrules section 8.6 ("capabilities are declared, not assumed") holds for the slot that hears
exactly as it does for chat: every `TranscriptionProvider` (an adapter that turns speech into text
on `ModelSlot.TRANSCRIBER`) declares up front whether it streams natively, whether it can time
single words, the longest clip it accepts and, when it is limited, the languages it knows; callers
read the declaration, never the provider's name. `check_request` is the one shared guard every
adapter runs first, so a clip that is too long or a language hint that is malformed or unsupported
fails the same typed way on every adapter, before any model work, network call or seat is spent.
`normalise_language` is the step before it for a hint from a device (roadmap step 10.5f): a
browser's `"en-US"` becomes the `"en"` that check accepts.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Declared by every adapter (`hivemind.llm.transcription.fake`,
    `hivemind.llm.providers.whisper`, `hivemind.llm.providers.openai_compat.transcription`) and
    read by callers deciding how to send audio. Calls into `hivemind.llm.errors` and
    `hivemind.llm.transcription.models` only.

Key invariants:
    - Every TranscriptionCapabilities is frozen and forbids unknown fields (codingrules 8.5).
    - `check_request` raises `ProviderRequestError` (the boundary's "rejected on its own terms,
      retrying unchanged will not help" error) with a synthesized status code, since the refusal
      happens before any HTTP exchange exists: 413 for a clip past `max_clip_s`, 400 for a
      language hint that is malformed or outside `languages`.
    - `normalise_language` never raises: a hint it cannot read becomes None (the model detects
      the language), so a device's locale never fails a transcription.
    - `streaming` False does not mean `stream()` is missing: such an adapter buffers the chunks
      and transcribes once (`hivemind.llm.transcription.buffered`), exactly as ADR-0033 decides.

See Also:
    - .claude/codingrules.md section 8.6 for "capabilities are declared, not assumed".
    - docs/adr/0033-transcription-provider-whisper-first.md for the capability list.
    - hivemind.llm.capabilities for ProviderCapabilities, the chat counterpart.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.transcription.models import LANGUAGE_PATTERN, AudioClip

DEFAULT_MAX_CLIP_S = 600.0  # Ten minutes: longer than any push-to-talk turn or `listen` window,
# and it keeps a 16 kHz mono clip under the 25 MiB upload cap hosted servers enforce.
CLIP_TOO_LONG_STATUS_CODE = 413  # "Payload Too Large", synthesized: no HTTP exchange happened.
BAD_LANGUAGE_STATUS_CODE = 400  # "Bad Request", synthesized for a malformed or unknown language.
MAX_LANGUAGE_CHARS = 32  # The longest language hint a device may send; never a sentence.
_MIN_LANGUAGE_SUBTAG_CHARS = 2  # No language code is a single letter.
_MAX_LANGUAGE_SUBTAG_CHARS = 3  # ISO 639-1 codes are two letters, ISO 639-2/3 three.
_LANGUAGE_RE = re.compile(LANGUAGE_PATTERN)  # Compiled once; checked on every language hint.

__all__ = [
    "BAD_LANGUAGE_STATUS_CODE",
    "CLIP_TOO_LONG_STATUS_CODE",
    "DEFAULT_MAX_CLIP_S",
    "MAX_LANGUAGE_CHARS",
    "TranscriptionCapabilities",
    "check_request",
    "normalise_language",
]


class TranscriptionCapabilities(BaseModel):
    """What one transcriber can do, declared up front so callers never branch on its name.

    Every field has a default: the defaults describe the plainest honest transcriber (no native
    streaming, no word timings, ten-minute clips, any language its model knows), and an adapter
    states only where it differs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    streaming: bool = Field(
        default=False,
        description="Whether the provider transcribes chunks as they arrive; False means "
        "stream() buffers every chunk and transcribes once, at the end.",
    )
    word_timestamps: bool = Field(
        default=False,
        description="Whether transcripts carry word-level timings; segments are per phrase "
        "either way.",
    )
    max_clip_s: float = Field(
        default=DEFAULT_MAX_CLIP_S, gt=0, description="The longest clip accepted, in seconds."
    )
    languages: tuple[Annotated[str, Field(pattern=LANGUAGE_PATTERN)], ...] | None = Field(
        default=None,
        description="The ISO 639 codes this provider transcribes; None for every language its "
        "model knows.",
    )


def check_request(
    provider: str,
    capabilities: TranscriptionCapabilities,
    clip: AudioClip,
    language: str | None,
) -> None:
    """Refuse a transcription `capabilities` says this provider cannot serve.

    Args:
        provider: The manifest `[llm.providers.<name>]` key, folded into the raised error.
        capabilities: The provider's own declared capabilities.
        clip: The audio about to be transcribed.
        language: The caller's ISO 639 language hint, or None to let the model detect it.

    Raises:
        ProviderRequestError: `clip` is longer than `capabilities.max_clip_s` (413), or
            `language` is not a lowercase ISO 639 code or not one of `capabilities.languages`
            (400).
    """
    # Length first: it is the check a caller most often trips (a long `listen` window), and the
    # detail names both figures so the caller can split the clip.
    if clip.duration_s > capabilities.max_clip_s:
        raise ProviderRequestError(
            provider,
            CLIP_TOO_LONG_STATUS_CODE,
            error_type="clip_too_long",
            detail=f"clip lasts {clip.duration_s:.1f}s; this provider accepts at most "
            f"{capabilities.max_clip_s:.1f}s",
        )
    if language is None:
        return  # No hint: the model detects the language itself, so there is nothing to check.
    if not _LANGUAGE_RE.match(language):
        raise ProviderRequestError(
            provider,
            BAD_LANGUAGE_STATUS_CODE,
            error_type="invalid_language",
            detail=f"language {language!r} is not a lowercase ISO 639 code such as 'en'",
        )
    if capabilities.languages is not None and language not in capabilities.languages:
        raise ProviderRequestError(
            provider,
            BAD_LANGUAGE_STATUS_CODE,
            error_type="unsupported_language",
            detail=f"language {language!r} is not one this provider declares",
        )


def normalise_language(language: str | None) -> str | None:
    """Reduce a device's language hint to the primary subtag `check_request` accepts.

    A device's locale arrives as `"en-US"` or `"EN"`; a transcriber takes `"en"`. A hint is
    advisory, so one that is not a plausible language code is dropped (the provider then detects
    the language itself) rather than refused.

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
