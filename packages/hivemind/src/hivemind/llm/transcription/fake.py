"""Provide FakeTranscription: an honest, scriptable TranscriptionProvider for tests and demos.

A fake transcriber answers from a scripted queue instead of a model, so the Entrance's voice route,
a Buzz demo or a `hive doctor` run can exercise everything above `hivemind.llm` with no server,
no model weights and no network (codingrules 14.4: fakes live in `src/` beside their protocol).
"Honest" means it never claims more than its declared `TranscriptionCapabilities`: a format it
does not list is rejected exactly as a real server rejects one (HTTP 415, as a typed
`ProviderRequestError`), segments are dropped when it declares none, and it names no language it
could not have detected. A scripted plain string becomes a transcript measured against the clip
it answers (the clip's duration, one segment spanning it), so a test scripts only the words.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Built by the registry
    for a `kind = "fake"` provider bound to `TRANSCRIBER`, and directly by tests. Imports this
    package's models, media and provider modules, `hivemind.llm.capabilities`,
    `hivemind.llm.errors` and `waggle.clock` only.

Key invariants:
    - The scripted queue is FIFO; an `LLMError` in it is raised, not returned, on its turn; an
      empty queue raises `ProviderUnavailableError`, never `IndexError`.
    - `set_outage(True)` makes every call raise `ProviderUnavailableError` before touching the
      queue or `calls`, and `health()` report DOWN.
    - `calls` records every clip and normalised language hint actually transcribed, in order;
      a clip refused for its format (or during an outage) was never transcribed and is absent.
    - `aclose()` releases nothing and changes nothing but `is_closed`.

See Also:
    - .claude/codingrules.md section 14.4 for the fakes-over-mocks rule.
    - hivemind.llm.fake for FakeLLMProvider, the chat fake whose shape this follows.
    - hivemind.llm.transcription.provider for the protocol this implements.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterable
from dataclasses import dataclass

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.errors import LLMError, ProviderRequestError, ProviderUnavailableError
from hivemind.llm.transcription.models import (
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptSegment,
    clip_from_chunks,
    normalise_language,
)
from hivemind.llm.transcription.provider import TranscriptionCapabilities
from waggle.clock import Clock, FakeClock

FAKE_DETECTED_LANGUAGE = "en"  # What the fake "detects" when nothing scripted says otherwise.
UNSUPPORTED_MEDIA_TYPE_STATUS = 415  # The status a real server answers an undecodable format with.

__all__ = [
    "FAKE_DETECTED_LANGUAGE",
    "UNSUPPORTED_MEDIA_TYPE_STATUS",
    "FakeTranscription",
    "TranscriptionCall",
]


@dataclass(frozen=True, slots=True)
class TranscriptionCall:
    """One clip the fake was asked to transcribe, with the language hint it received."""

    clip: AudioClip  # The clip exactly as passed in.
    language: str | None  # The hint after normalise_language; None when absent or dropped.


class FakeTranscription:
    """A scriptable, honestly-capability-limited TranscriptionProvider.

    Like `hivemind.llm.fake.FakeLLMProvider`, meant to be scripted by one test before the code
    under test runs; not guarded against concurrent scripting.
    """

    def __init__(
        self,
        name: str = "fake",
        capabilities: TranscriptionCapabilities | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Create a FakeTranscription with nothing scripted yet.

        Args:
            name: This provider's manifest-style name.
            capabilities: What to declare; `TranscriptionCapabilities.full()` when omitted, so a
                test opts into a weaker shape explicitly.
            clock: Source of `health()`'s `checked_at`; a fresh FakeClock when omitted.
        """
        self._name = name
        self._capabilities = (
            capabilities if capabilities is not None else TranscriptionCapabilities.full()
        )
        self._clock: Clock = clock if clock is not None else FakeClock()
        self._script: deque[Transcript | str | LLMError] = deque()
        self._is_down = False
        self._is_closed = False
        self.calls: list[TranscriptionCall] = []

    @property
    def name(self) -> str:
        """Return this provider's name; see `TranscriptionProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return the declared capabilities; see `TranscriptionProvider.capabilities`."""
        return self._capabilities

    @property
    def is_closed(self) -> bool:
        """Return whether `aclose()` has been called at least once."""
        return self._is_closed

    def script(self, *answers: Transcript | str | LLMError) -> None:
        """Queue answers to return in order, one per transcription.

        Args:
            answers: A `Transcript` is returned as scripted (less anything the capabilities
                forbid); a `str` becomes a transcript of that text spanning the whole clip; an
                `LLMError` is raised on its turn.
        """
        self._script.extend(answers)

    def set_outage(self, is_down: bool) -> None:
        """Simulate the provider being down (every call raises) or recovered.

        Args:
            is_down: True to fail every call and report DOWN; False to recover.
        """
        self._is_down = is_down

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Return the next scripted answer for `clip`; see `TranscriptionProvider.transcribe`."""
        if self._is_down:
            raise ProviderUnavailableError(self._name, "an outage is simulated")
        if clip.media_type not in self._capabilities.media_types:
            # A real adapter refuses a container it cannot decode before uploading anything, so
            # an honest fake refuses it before recording a call.
            raise ProviderRequestError(
                self._name,
                UNSUPPORTED_MEDIA_TYPE_STATUS,
                error_type="unsupported_media_type",
                detail=f"{clip.media_type.value} is not a format this provider decodes",
            )
        hint = normalise_language(language)
        self.calls.append(TranscriptionCall(clip=clip, language=hint))
        return self._honour_capabilities(self._next_answer(clip), hint)

    async def stream(
        self, chunks: AsyncIterable[AudioChunk], language: str | None = None
    ) -> Transcript:
        """Gather the stream into one clip and transcribe it; see `TranscriptionProvider.stream`."""
        clip = await clip_from_chunks(chunks)
        return await self.transcribe(clip, language)

    async def health(self) -> ProviderHealth:
        """Return DOWN during a simulated outage, HEALTHY otherwise."""
        if self._is_down:
            return ProviderHealth(
                state=HealthState.DOWN, detail="set_outage(True)", checked_at=self._clock.now()
            )
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())

    async def aclose(self) -> None:
        """Record the close; there is nothing to release. See `TranscriptionProvider.aclose`."""
        self._is_closed = True

    def _next_answer(self, clip: AudioClip) -> Transcript:
        """Pop the next scripted answer, raising a scripted error and expanding a plain string.

        Raises:
            ProviderUnavailableError: The queue is empty; script more answers first.
        """
        if not self._script:
            raise ProviderUnavailableError(
                self._name, "the scripted transcript queue ran dry: call script(...) first"
            )
        answer = self._script.popleft()
        if isinstance(answer, LLMError):
            raise answer
        if isinstance(answer, str):
            # Measured against the clip it answers, as a real transcript would be.
            segment = TranscriptSegment(start_s=0.0, end_s=clip.duration_s, text=answer)
            return Transcript(
                text=answer,
                language=FAKE_DETECTED_LANGUAGE,
                duration_s=clip.duration_s,
                segments=(segment,),
            )
        return answer

    def _honour_capabilities(self, transcript: Transcript, hint: str | None) -> Transcript:
        """Drop what the declared capabilities say this provider could not have produced."""
        # A hint is echoed back, as servers do; without one only a detecting provider names one.
        language: str | None
        if hint is not None:
            language = hint
        elif self._capabilities.language_detection:
            language = transcript.language
        else:
            language = None
        segments = transcript.segments if self._capabilities.segments else ()
        return transcript.model_copy(update={"language": language, "segments": segments})
