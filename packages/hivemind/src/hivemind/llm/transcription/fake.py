"""Provide FakeTranscription: an honest, scriptable TranscriptionProvider for tests and demos.

A fake transcriber answers from a script instead of a model, so a unit test, a `hive doctor` run
or a demo can exercise everything that hears (Buzz's `listen` tool, the Exoskeleton's ears; the
Hive Entrance's voice route, roadmap step 10.5f) with no model, no extra installed and no network
(codingrules 14.4: fakes live in src/ beside their Protocol). "Honest" means it keeps the same
promises a real adapter keeps: it refuses a clip past its declared `max_clip_s` and a malformed or
unsupported language hint the same typed way (`check_request`), it streams by buffering exactly
as a real adapter without native streaming does, and whatever it answers reports the clip's own
duration and the caller's language hint, as every real adapter's transcript does. Scripted
transcripts are returned in order (a plain string is a transcript of that text spanning the whole
clip); once the script runs dry, every clip gets the default transcript: the one the caller gave,
or silence (no text, no segments) spanning the clip.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Built by the provider registry for `kind = "fake"` (`hivemind.llm.registry`) and by tests.
    Calls into `hivemind.llm.capabilities`, `hivemind.llm.errors`, this package's `buffered`,
    `capabilities` and `models`, and `waggle.clock` only.

Key invariants:
    - `calls` records what each transcription was asked for (duration, size, format, language),
      never the audio itself: even a fake does not keep a clip past its call (ADR-0033).
    - The script is FIFO; an `LLMError` in it is raised, not returned, on its turn.
    - An answered transcript's `duration_s` is the clip's and, when the caller gave a language
      hint, its `language` is the hint: a scripted value never contradicts what a real adapter
      would report for the same call.
    - `set_outage(True)` makes every call raise `ProviderUnavailableError` before touching the
      script or `calls`, and makes `health()` report DOWN.

See Also:
    - .claude/codingrules.md section 14.4 for the fakes-over-mocks rule.
    - hivemind.llm.fake for FakeLLMProvider, the chat fake this mirrors.
    - hivemind.llm.transcription.provider for the Protocol this implements.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.errors import LLMError, ProviderUnavailableError
from hivemind.llm.transcription.buffered import stream_by_buffering
from hivemind.llm.transcription.capabilities import TranscriptionCapabilities, check_request
from hivemind.llm.transcription.media import AudioMediaType
from hivemind.llm.transcription.models import AudioChunk, AudioClip, Transcript, TranscriptSegment
from waggle.clock import Clock, FakeClock

__all__ = ["FakeTranscription", "FakeTranscriptionCall"]


@dataclass(frozen=True, slots=True)
class FakeTranscriptionCall:
    """What one transcription asked for, kept without the audio itself."""

    duration_s: float  # The clip's own duration, in seconds.
    size_bytes: int  # The clip's file size, so a test can check what a stream assembled.
    language: str | None  # The caller's language hint, or None.
    media_type: AudioMediaType = AudioMediaType.WAV  # The clip's format, as its sender labelled it.


class FakeTranscription:
    """A scriptable, honestly-limited TranscriptionProvider for tests and demos.

    Scripting and calls are meant to be set up by one test before use, then read by the code
    under test, the same single-owner pattern `hivemind.llm.fake.FakeLLMProvider` documents.
    """

    def __init__(
        self,
        name: str = "fake",
        capabilities: TranscriptionCapabilities | None = None,
        clock: Clock | None = None,
        default: Transcript | None = None,
    ) -> None:
        """Create a FakeTranscription with nothing scripted yet.

        Args:
            name: This provider's manifest-style name; "fake" by default.
            capabilities: What to declare; the plainest honest shape
                (`TranscriptionCapabilities()`) when omitted.
            clock: Source of `health()`'s `checked_at`; a fresh FakeClock when omitted.
            default: Returned once the script runs dry; None means silence spanning the clip.
        """
        self._name = name
        self._capabilities = (
            capabilities if capabilities is not None else TranscriptionCapabilities()
        )
        self._clock: Clock = clock if clock is not None else FakeClock()
        self._default = default
        self._script: deque[Transcript | str | LLMError] = deque()
        self._is_down = False
        self.calls: list[FakeTranscriptionCall] = []

    @property
    def name(self) -> str:
        """Return this provider's name; see `TranscriptionProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return this provider's declared capabilities."""
        return self._capabilities

    def script(self, *items: Transcript | str | LLMError) -> None:
        """Queue transcripts (or errors) to answer with, in order, one per call.

        Args:
            items: Appended to the FIFO queue; an `LLMError` is raised when its turn comes, and a
                `str` becomes a transcript of that text spanning the whole clip it answers.
        """
        self._script.extend(items)

    def set_outage(self, is_down: bool) -> None:
        """Simulate the provider being entirely down (or recovered).

        Args:
            is_down: When True, every call raises `ProviderUnavailableError` and `health()`
                reports DOWN.
        """
        self._is_down = is_down

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Answer with the next scripted transcript; see `TranscriptionProvider.transcribe`."""
        if self._is_down:
            raise ProviderUnavailableError(self._name, "an outage is simulated via set_outage")
        # The same refusal a real adapter makes, before the script is touched or a call recorded.
        check_request(self._name, self._capabilities, clip, language)
        call = FakeTranscriptionCall(clip.duration_s, len(clip.data), language, clip.media_type)
        self.calls.append(call)
        return _as_reported(self._next_answer(clip, language), clip, language)

    def stream(
        self, chunks: AsyncIterator[AudioChunk], language: str | None = None
    ) -> AsyncIterator[TranscriptSegment]:
        """Buffer `chunks`, transcribe once, yield the segments; see `TranscriptionProvider.stream`.

        No native streaming, declared honestly (`capabilities.streaming` is False by default), so
        this is the same buffered path every such adapter takes.
        """
        return stream_by_buffering(self.transcribe, chunks, language, self._name)

    async def health(self) -> ProviderHealth:
        """Return DOWN while an outage is simulated, HEALTHY otherwise."""
        if self._is_down:
            return ProviderHealth(
                state=HealthState.DOWN, detail="set_outage(True)", checked_at=self._clock.now()
            )
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())

    def _next_answer(self, clip: AudioClip, language: str | None) -> Transcript:
        """Pop the next scripted answer (raising a scripted error), or fall back to the default.

        Raises:
            LLMError: The next scripted item is an error.
        """
        if not self._script:
            # The script ran dry: the caller's default, or silence spanning the clip.
            if self._default is not None:
                return self._default
            return Transcript(text="", language=language, duration_s=clip.duration_s)
        item = self._script.popleft()
        if isinstance(item, LLMError):
            raise item
        if isinstance(item, str):
            # Measured against the clip it answers, as a real transcript would be.
            segment = TranscriptSegment(start_s=0.0, end_s=clip.duration_s, text=item)
            return Transcript(
                text=item, language=language, duration_s=clip.duration_s, segments=(segment,)
            )
        return item


def _as_reported(transcript: Transcript, clip: AudioClip, language: str | None) -> Transcript:
    """Return `transcript` as a real adapter would report it: the clip's duration, the hint.

    Args:
        transcript: The scripted or default answer, as written.
        clip: The clip this call transcribed; its duration is the transcript's.
        language: The caller's hint; when given, it is the transcript's language.

    Returns:
        `transcript` itself when it already agrees, otherwise a copy that does.
    """
    update: dict[str, object] = {"duration_s": clip.duration_s}
    if language is not None:
        update["language"] = language
    if all(getattr(transcript, key) == value for key, value in update.items()):
        return transcript
    return transcript.model_copy(update=update)
