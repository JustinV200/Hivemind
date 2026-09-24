"""Define TranscriptionProvider: the one protocol every speech-to-text call goes through.

Codingrules section 8.6's "one door" rule, applied to the slot that hears: `ModelSlot.TRANSCRIBER`
is audio in, text out, a non-chat slot with its own protocol rather than a chat call with an audio
part (ADR-0033: only a few chat models accept audio, and the slot's metering and fallback rules
would bend around a model that is not a chat model). This module is that door: a `typing.Protocol`
with no logic of its own, implemented by `hivemind.llm.providers.whisper.WhisperLocalTranscription`
(faster-whisper in process), `hivemind.llm.providers.openai_compat.OpenAICompatTranscription` (any
server speaking `/audio/transcriptions`) and `hivemind.llm.transcription.fake.FakeTranscription`.
`stream` is declared with a plain `def`, exactly as `hivemind.llm.provider.LLMProvider.stream` is,
so an implementation may return an async generator (its own, or the buffered helper's) without
the caller having to await anything before iterating.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Called through a `hivemind.llm.transcription.gate.TranscriptionGate` (the Fanner's, the seat
    meter every model call passes through, or the direct one) by Buzz's `listen` tool (the
    Exoskeleton's ears, roadmap step 6.5) and the Hive Entrance's voice route (10.5f).
    Implementations call into their own `llm/providers/<name>/` only.

Key invariants:
    - `name` is the manifest's `[llm.providers.<name>]` key, for logs, the trail and error
      messages; nothing branches on it (codingrules section 8.6).
    - Every failure a caller can handle is a `hivemind.llm.errors.LLMError`, never a library or
      HTTP exception: each adapter translates its own.
    - No implementation keeps audio past the call, logs it, or writes it or the transcript text
      to the Pheromone Trail (ADR-0033: audio is transient and personal).

See Also:
    - .claude/codingrules.md section 8.6 for the provider independence rules this protocol obeys.
    - docs/adr/0033-transcription-provider-whisper-first.md for the decision this implements.
    - hivemind.llm.provider for LLMProvider, the chat counterpart this mirrors.
    - hivemind.llm.transcription.models for AudioClip, AudioChunk, Transcript, TranscriptSegment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.transcription.capabilities import TranscriptionCapabilities
from hivemind.llm.transcription.models import AudioChunk, AudioClip, Transcript, TranscriptSegment

__all__ = ["TranscriptionProvider"]


class TranscriptionProvider(Protocol):
    """Turn speech into text for one provider: transcribe, stream, and report health.

    Implementations must be safe to call concurrently; one whose model can serve a single call
    at a time (an in-process model is one seat) queues the rest itself.
    """

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; never branch on it."""
        ...

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return this provider's declared capabilities, fixed for the instance's life."""
        ...

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Transcribe one whole clip.

        Args:
            clip: The audio, already validated against its own WAV header.
            language: A lowercase ISO 639 hint ("en"), or None to let the model detect it.

        Returns:
            The transcript: its text, language, the clip's duration and time-ordered segments.

        Raises:
            ProviderRequestError: The request was refused on its own terms: the clip is past
                `capabilities.max_clip_s`, the language hint is malformed or unsupported, or
                the provider rejected the audio.
            RateLimitedError: The provider refused the call due to a rate limit.
            ProviderUnavailableError: The provider could not be reached, or its model could not
                load or run.
        """
        ...

    def stream(
        self, chunks: AsyncIterator[AudioChunk], language: str | None = None
    ) -> AsyncIterator[TranscriptSegment]:
        """Transcribe a push-to-talk stream, yielding segments as they become available.

        Args:
            chunks: Raw PCM pieces in arrival order, all at one sample rate and channel count;
                the stream ends when the speaker does. The caller owns its pacing and deadline.
            language: A lowercase ISO 639 hint, or None to let the model detect it.

        Returns:
            An async iterator of TranscriptSegment. A provider whose `capabilities.streaming` is
            False buffers every chunk and yields the whole transcript's segments once the stream
            ends; an empty stream yields nothing.

        Raises:
            ProviderRequestError: The chunks disagree on their format or outgrow one clip, or
                `transcribe` would refuse the assembled clip.
            RateLimitedError: As for `transcribe`.
            ProviderUnavailableError: As for `transcribe`.
        """
        ...

    async def health(self) -> ProviderHealth:
        """Return this provider's current health, cheaply: never a full transcription.

        Returns:
            A fresh ProviderHealth reading (Appendix C's "Provider health" machine).
        """
        ...
