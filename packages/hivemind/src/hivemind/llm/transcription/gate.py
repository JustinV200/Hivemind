"""Define TranscriptionGate: the one seam every transcription call passes through.

A caller that hears (Buzz's `listen` tool, the Exoskeleton's ears; the Hive Entrance's voice
route, roadmap step 10.5f) never calls `bound.provider.transcribe` itself. It calls a
`TranscriptionGate`, the transcription counterpart of `hivemind.llm.ladders.gate.CallGate`, so
the Fanner (the seat meter every model call passes through) can sit behind every call as a
composition-root choice: `hivemind.llm.fanner.transcription.FannerTranscriptionGate` meters,
spills and records; `DirectTranscriptionGate` here does neither. Both walk the binding's fallback
chain by the same rule, because no degradation ladder sits above a transcription the way one sits
above a chat call: a `ProviderUnavailableError` or `RateLimitedError` moves the call to the next
binding, and the last binding's error reaches the caller unchanged.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Implemented here by `DirectTranscriptionGate` and in `hivemind.llm.fanner.transcription` by
    the Fanner's gate; called by whoever holds a `BoundTranscriber`. Calls into
    `hivemind.llm.errors` and this package's `binding` and `models` only.

Key invariants:
    - Only an outage or a rate limit moves a call along the chain; a `ProviderRequestError` (the
      request itself was refused) propagates at once, since the next binding would refuse the
      same clip for the same reason.
    - `DirectTranscriptionGate` adds no metering and records nothing: it is the gate for tests and
      for callers that are deliberately outside the Fanner.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for "metered by the Fanner".
    - hivemind.llm.ladders.gate for CallGate, the chat seam this mirrors.
    - hivemind.llm.fanner.transcription for the metered implementation.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.llm.transcription.binding import BoundTranscriber
from hivemind.llm.transcription.models import AudioClip, Transcript

__all__ = ["DirectTranscriptionGate", "TranscriptionGate"]


class TranscriptionGate(Protocol):
    """Transcribe one clip through a BoundTranscriber chain; the seam a meter can sit behind."""

    async def transcribe(
        self, bound: BoundTranscriber, clip: AudioClip, language: str | None = None
    ) -> Transcript:
        """Transcribe `clip` on `bound`, moving along its fallback chain on an outage.

        Args:
            bound: The first link of the chain; its provider and model decide who hears.
            clip: The audio, already validated against its own WAV header.
            language: A lowercase ISO 639 hint, or None to let the model detect it.

        Returns:
            The transcript from the first link that answered.

        Raises:
            ProviderRequestError: A provider refused the request itself; never retried.
            RateLimitedError: The last link in the chain was rate-limited.
            ProviderUnavailableError: The last link in the chain could not be reached or run.
        """
        ...


class DirectTranscriptionGate:
    """Call each link's provider directly, with no metering and no trail events."""

    async def transcribe(
        self, bound: BoundTranscriber, clip: AudioClip, language: str | None = None
    ) -> Transcript:
        """Transcribe through the chain unmetered; see `TranscriptionGate.transcribe`."""
        current = bound
        # One attempt per link, in chain order; the loop ends on the first transcript or on the
        # last link's own error.
        while True:
            try:
                # External await: the provider's transcription, bounded by its own timeout.
                return await current.provider.transcribe(clip, language)
            except (ProviderUnavailableError, RateLimitedError):
                if current.fallback is None:
                    raise  # The chain is exhausted; the caller sees the last link's own error.
                current = current.fallback
