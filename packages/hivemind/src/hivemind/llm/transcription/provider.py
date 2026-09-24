"""Define TranscriptionProvider: the one door audio takes into text, and what a provider can do.

Codingrules section 8.6 puts every model behind a protocol so a slot can move from a hosted API to
a local server without touching code above `hivemind.llm`; `TRANSCRIBER` is the non-chat slot for
audio in, text out, and this is its protocol, the counterpart of `hivemind.llm.provider.
LLMProvider`. An implementation turns an `AudioClip` into a `Transcript` (`transcribe`), gathers a
push-to-talk stream into one (`stream`), reports its health and releases its connections at
shutdown. `TranscriptionCapabilities` declares what it can do -- timestamped segments, language
detection, which formats it decodes -- so a caller branches on capabilities, never on the
provider's name.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Implemented by
    `hivemind.llm.transcription.fake.FakeTranscription`, `hivemind.llm.providers.openai_compat.
    OpenAICompatTranscription` and, metered, `hivemind.llm.fanner.MeteredTranscriber`; called by
    the Entrance's voice route (roadmap step 10.5f) and later by Buzz (roadmap step 6.5).

Key invariants:
    - `stream` gathers every chunk into one clip and transcribes that
      (`hivemind.llm.transcription.models.clip_from_chunks`) in every implementation today; a
      provider that recognises speech incrementally can do better behind the same signature.
    - Every failure a caller can act on is typed: `hivemind.llm.errors`'s `ProviderUnavailableError`
      (unreachable or down), `RateLimitedError` (wait) and `ProviderRequestError` (the provider
      rejected this clip, a format it does not decode included), plus `InvalidAudioClipError` from
      `stream` when the chunks do not make a clip. Never a library's own exception.
    - `name` identifies the provider in logs and on the trail; nothing branches on it.

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule.
    - .claude/codingrules.md section 8.6 for model slots and capability declarations.
    - hivemind.llm.provider for LLMProvider, the chat counterpart of this protocol.
    - hivemind.llm.transcription.models for AudioClip, AudioChunk and Transcript.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.transcription.media import AudioMediaType
from hivemind.llm.transcription.models import AudioChunk, AudioClip, Transcript

__all__ = ["TranscriptionCapabilities", "TranscriptionProvider"]


class TranscriptionCapabilities(BaseModel):
    """What one transcription provider can do, declared so callers never guess from its name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segments: bool = Field(
        description="Whether a Transcript carries timestamped segments; False means the text only."
    )
    language_detection: bool = Field(
        description="Whether the provider names the language spoken when given no hint."
    )
    media_types: frozenset[AudioMediaType] = Field(
        min_length=1, description="Every format this provider decodes; never empty."
    )

    @classmethod
    def full(cls) -> TranscriptionCapabilities:
        """Return the strongest shape: segments, language detection, every accepted format.

        Returns:
            What a Whisper-style server answering `verbose_json` declares.
        """
        return cls(segments=True, language_detection=True, media_types=frozenset(AudioMediaType))

    @classmethod
    def none(cls) -> TranscriptionCapabilities:
        """Return the weakest usable shape: plain text from WAV only.

        Every caller must still work against this, the way every ladder works against
        `ProviderCapabilities.none()`.

        Returns:
            Capabilities with no segments, no language detection and WAV as the only format.
        """
        return cls(
            segments=False,
            language_detection=False,
            media_types=frozenset({AudioMediaType.WAV}),
        )


class TranscriptionProvider(Protocol):
    """Turn audio into text: transcribe a clip or a stream, report health, and close.

    Implementations must be safe to call concurrently: several devices may speak at once.
    """

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; for identification only."""
        ...

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return this provider's declared capabilities; fixed for the instance's life."""
        ...

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Transcribe one clip.

        Args:
            clip: The recording; its format must be one of `capabilities.media_types`.
            language: A language hint (`"en"`, or a locale such as `"en-US"`, reduced by
                `normalise_language`); None lets the provider detect the language.

        Returns:
            What was heard. `segments` is empty unless `capabilities.segments`; `language` is
            the hint when one was given, the detected language when `capabilities.
            language_detection`, and None otherwise.

        Raises:
            ProviderUnavailableError: The provider could not be reached or is down.
            RateLimitedError: The provider refused the call because of a rate limit.
            ProviderRequestError: The provider rejected this clip (a format it does not decode is
                HTTP 415), or its answer could not be read as a transcript.
        """
        ...

    async def stream(
        self, chunks: AsyncIterable[AudioChunk], language: str | None = None
    ) -> Transcript:
        """Transcribe one push-to-talk stream: its frames gathered into one clip.

        Args:
            chunks: The stream's frames, in order.
            language: A language hint, as for `transcribe`.

        Returns:
            What was heard in the whole stream.

        Raises:
            InvalidAudioClipError: The frames do not make an acceptable clip.
            ProviderUnavailableError: As for `transcribe`.
            RateLimitedError: As for `transcribe`.
            ProviderRequestError: As for `transcribe`.
        """
        ...

    async def health(self) -> ProviderHealth:
        """Return a fresh health reading; never raises (every failure is a reading)."""
        ...

    async def aclose(self) -> None:
        """Release every connection this provider holds; idempotent, and its last call.

        The owner (`hivemind.llm.registry.ProviderRegistry.aclose`) calls it at shutdown, the
        same contract as `hivemind.llm.provider.LLMProvider.aclose`.
        """
        ...
