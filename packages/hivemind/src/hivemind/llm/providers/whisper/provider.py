"""Define WhisperLocalTranscription: speech to text in the Hive's own process, on faster-whisper.

The `TranscriptionProvider` for `kind = "whisper_local"` (roadmap step 6.5a, ADR-0033): one
instance holds one loaded Whisper model, loaded lazily on the first transcription (construction
never touches the disk or the network, so the registry can build it cheaply and `health()` can
report a missing extra instead of crashing an import). One loaded model is one seat: an
`asyncio.Lock` lets exactly one transcription (or the first load) use the model at a time, and
the rest queue. Loading and inference both block for seconds to minutes, so both run on a worker
thread (`asyncio.to_thread`) under a timeout (codingrules section 11); every library failure is
translated into the boundary's typed errors here, and no library type leaves this module.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.whisper`.
    Built by the provider registry (`hivemind.llm.registry`) for `ModelSlot.TRANSCRIBER`'s
    bindings, called through a transcription gate (the Fanner's, the seat meter every model call
    passes through). Calls into this package's `loader` and `mapping`, `hivemind.llm.errors`,
    `hivemind.llm.capabilities`, `hivemind.llm.transcription` and `waggle.clock`.

Key invariants:
    - Audio lives in memory for the call only: the clip is handed to the model as an in-memory
      file and dropped when the call returns; it is never written to disk or logged.
    - A timed-out load or run cannot be interrupted (a thread cannot be killed); the call raises
      at the deadline and the worker finishes in the background. The library queues any run that
      overlaps a late one on the same model, so it delays the next run, never corrupts it; a late
      load's result is simply dropped, and the next call loads afresh.
    - A failed load is remembered only for `health()`: the next transcription tries to load
      again, since a missing download or a busy GPU may have been transient.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the decision this implements.
    - hivemind.llm.providers.whisper.loader for the lazy import and the device rule.
    - hivemind.llm.transcription.provider for the TranscriptionProvider Protocol.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator

from pydantic import ValidationError

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.errors import (
    MalformedOutputError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from hivemind.llm.providers.whisper import mapping
from hivemind.llm.providers.whisper.config import WhisperConfig
from hivemind.llm.providers.whisper.loader import (
    LOAD_FAILURES,
    FasterWhisperLoader,
    LoadedModel,
    ModelLoader,
)
from hivemind.llm.transcription import (
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptionCapabilities,
    TranscriptSegment,
    check_request,
    stream_by_buffering,
)
from waggle.clock import Clock

REFUSED_AUDIO_STATUS_CODE = 400  # "Bad Request", synthesized: the model refused this clip itself.
MAX_DETAIL_CHARS = 200  # Enough of a library message to diagnose; never a wall of stack text.

__all__ = ["MAX_DETAIL_CHARS", "REFUSED_AUDIO_STATUS_CODE", "WhisperLocalTranscription"]


class WhisperLocalTranscription:
    """Transcribe in process with one lazily loaded faster-whisper model: one seat.

    Owns its loaded model and the lock that makes it one seat (codingrules section 11: shared
    mutable state has one owner and a documented lock). Never branch on `self.name`.
    """

    def __init__(
        self,
        name: str,
        config: WhisperConfig,
        clock: Clock,
        loader: ModelLoader | None = None,
    ) -> None:
        """Remember how to load the model; load nothing yet.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: Which model, where, at what precision, and the load and run timeouts.
            clock: Source of `health()`'s `checked_at`.
            loader: How to load the model; faster-whisper when omitted. A test passes one that
                returns a stand-in model, so no real model is ever loaded or downloaded.
        """
        self._name = name
        self._config = config
        self._clock = clock
        self._loader: ModelLoader = loader if loader is not None else FasterWhisperLoader()
        self._loaded: LoadedModel | None = None
        self._load_failure: str | None = None
        # The one seat: one transcription, or the first load, uses the model at a time; waiting
        # here (not in the library) keeps the queue visible to asyncio and to cancellation.
        self._seat = asyncio.Lock()

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; never branch on it."""
        return self._name

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return this provider's declared capabilities, from its config."""
        return self._config.capabilities

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Transcribe `clip` on the loaded model; see `TranscriptionProvider.transcribe`."""
        # Refuse what the declaration rules out before queueing for the seat or loading anything.
        check_request(self._name, self._config.capabilities, clip, language)
        async with self._seat:
            loaded = await self._ensure_loaded()
            return await self._run(loaded, clip, language)

    def stream(
        self, chunks: AsyncIterator[AudioChunk], language: str | None = None
    ) -> AsyncIterator[TranscriptSegment]:
        """Buffer `chunks` and transcribe once; see `TranscriptionProvider.stream`.

        faster-whisper decodes whole files, so this adapter declares no native streaming and
        takes the shared buffered path (ADR-0033).
        """
        return stream_by_buffering(self.transcribe, chunks, language, self._name)

    async def health(self) -> ProviderHealth:
        """Report DOWN without the library or after a failed load, HEALTHY otherwise.

        Never loads a model: a probe must stay cheap, so an unloaded model reads HEALTHY with a
        note that it loads on first use.
        """
        missing = self._loader.missing_reason()
        if missing is not None:
            return self._reading(HealthState.DOWN, missing)
        if self._load_failure is not None:
            return self._reading(HealthState.DOWN, self._load_failure)
        if self._loaded is not None:
            detail = f"model loaded on {self._loaded.device} ({self._loaded.compute_type})"
            return self._reading(HealthState.HEALTHY, detail)
        return self._reading(HealthState.HEALTHY, "installed; the model loads on first use")

    async def _ensure_loaded(self) -> LoadedModel:
        """Return the loaded model, loading it on a worker thread the first time.

        Raises:
            ProviderUnavailableError: The library is missing, or the load failed or timed out.
        """
        if self._loaded is not None:
            return self._loaded
        try:
            # External await: reading (and maybe downloading) the weights, seconds to minutes;
            # on timeout the load is abandoned and the call raises, and the next one retries.
            async with asyncio.timeout(self._config.load_timeout_s):
                loaded = await asyncio.to_thread(self._loader.load, self._config)
        except ImportError as exc:
            raise self._unavailable(self._loader.missing_reason() or str(exc)) from exc
        except TimeoutError as exc:
            detail = f"model did not load within {self._config.load_timeout_s:.0f}s"
            raise self._unavailable(detail) from exc
        except LOAD_FAILURES as exc:
            raise self._unavailable(f"model failed to load: {_brief(exc)}") from exc
        self._loaded, self._load_failure = loaded, None
        return loaded

    async def _run(self, loaded: LoadedModel, clip: AudioClip, language: str | None) -> Transcript:
        """Transcribe on a worker thread under the run timeout, translating library errors.

        Raises:
            ProviderRequestError: The model refused the clip or the language itself.
            ProviderUnavailableError: The run failed on the model's side, or timed out.
            MalformedOutputError: The model's output broke a transcript bound (runaway text).
        """
        try:
            # External await: the whole decode, seconds per minute of audio on a CPU; see the
            # module docstring for what a timeout leaves running.
            async with asyncio.timeout(self._config.timeout_s):
                return await asyncio.to_thread(_transcribe_blocking, loaded.model, clip, language)
        except TimeoutError as exc:
            detail = f"transcription did not finish within {self._config.timeout_s:.0f}s"
            raise ProviderUnavailableError(self._name, detail) from exc
        except ValidationError as exc:
            # Checked before the refusals (it is a ValueError too): the model ran, but its output
            # broke a transcript bound. The error quotes the words, so only a count is kept and
            # the cause is dropped (`from None`) rather than chained into a loggable traceback.
            raw = f"output broke {exc.error_count()} transcript bound(s)"
            raise MalformedOutputError(self._name, raw=raw, attempts=1) from None
        except loaded.refusals as exc:
            raise ProviderRequestError(
                self._name,
                REFUSED_AUDIO_STATUS_CODE,
                error_type="audio_refused",
                detail=_brief(exc),
            ) from exc
        except loaded.faults as exc:
            raise ProviderUnavailableError(self._name, f"the model failed: {_brief(exc)}") from exc

    def _unavailable(self, detail: str) -> ProviderUnavailableError:
        """Remember a failed load for `health()` and build the error that reports it."""
        self._load_failure = detail
        return ProviderUnavailableError(self._name, detail)

    def _reading(self, state: HealthState, detail: str) -> ProviderHealth:
        """Build one health reading stamped with the injected clock."""
        return ProviderHealth(state=state, detail=detail, checked_at=self._clock.now())


def _transcribe_blocking(
    model: mapping.WhisperModelLike, clip: AudioClip, language: str | None
) -> Transcript:
    """Run one transcription to completion; blocking, so it only ever runs on a worker thread.

    The library decodes lazily, so `mapping.to_transcript` consuming the segments is the part
    that actually runs the model; both happen here, on the same thread.
    """
    segments, info = model.transcribe(io.BytesIO(clip.data), language=language)
    return mapping.to_transcript(segments, info, clip.duration_s, language)


def _brief(exc: BaseException) -> str:
    """Render a short, single-line description of a library exception for an error message."""
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return text if len(text) <= MAX_DETAIL_CHARS else text[:MAX_DETAIL_CHARS] + "..."
