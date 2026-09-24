"""Provide one TranscriptionHarness per TranscriptionProvider, for the transcription contract suite.

`test_transcription_provider_contract.py` (roadmap step 6.5a) states each clause of the
`hivemind.llm.transcription.TranscriptionProvider` contract once and runs it over every
implementation this module registers a harness for: the fake (`FakeHarness`, scripting
`FakeTranscription` directly), the OpenAI-compatible adapter (`OpenAICompatHarness`, served by
`httpx.MockTransport` from the recorded reply under `tests/fixtures/llm/openai_compat/`), and the
in-process Whisper adapter (`WhisperHarness`, handed a stand-in model at its own `ModelLoader`
seam, so no real model is loaded or downloaded). Every harness hears the same canonical speech
(`CANONICAL_TEXT`, two segments, the recorded reply's own content) so one assertion fits all
three, and each says honestly when it cannot simulate a failure (Whisper in process has no rate
limit to hit).

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used only by
    `contracts.test_transcription_provider_contract`.

Key invariants:
    - Every harness resets all its mutable state in `make_provider()`, so one module-level
      instance per adapter is safe to reuse across every parametrised test.
    - Model ids here are neutral (`MODEL`); no clip here is a recording of anyone
      (`builders.audio` synthesises them).

See Also:
    - contracts.test_transcription_provider_contract for the clauses built on this module.
    - contracts.llm_provider_harness for the chat harnesses this module mirrors.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Literal, Protocol

import httpx
from builders.audio import StandInLoader, StandInSegment, StandInWhisperModel

from hivemind.llm import (
    ProviderUnavailableError,
    RateLimitedError,
    Transcript,
    TranscriptionCapabilities,
    TranscriptionProvider,
    TranscriptSegment,
)
from hivemind.llm.providers.openai_compat import (
    OpenAICompatTranscription,
    OpenAICompatTranscriptionConfig,
)
from hivemind.llm.providers.whisper import WhisperConfig, WhisperLocalTranscription
from hivemind.llm.transcription import FakeTranscription
from waggle.clock import FakeClock

MODEL = "speech-model"  # Neutral id, never a real provider's (codingrules 8.6).
RETRY_AFTER_S = 2.0  # What every rate-limited arrangement reports.
CANONICAL_TEXT = "Hello from the hive."  # The recorded reply's own text.
CANONICAL_SEGMENTS = (("Hello from", 0.0, 0.48, -0.21), ("the hive.", 0.48, 0.96, -0.35))

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "llm" / "openai_compat"
_BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; the mock transport answers.
_TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
_MODELS_PATH = "/v1/models"
_SILENCE_REPLY = {"text": "", "segments": []}  # What a server answers for a clip with no speech.

# One queued reply for the mock transport: (status, body bytes, headers).
_Reply = tuple[int, bytes, dict[str, str]]

ErrorKind = Literal["unavailable", "rate_limited"]

__all__ = [
    "CANONICAL_SEGMENTS",
    "CANONICAL_TEXT",
    "MODEL",
    "RETRY_AFTER_S",
    "ErrorKind",
    "FakeHarness",
    "OpenAICompatHarness",
    "TranscriptionHarness",
    "WhisperHarness",
]


class TranscriptionHarness(Protocol):
    """Build one TranscriptionProvider and script what its model or server hears next."""

    def make_provider(
        self, capabilities: TranscriptionCapabilities | None = None
    ) -> TranscriptionProvider:
        """Build a fresh provider; `capabilities` defaults to the adapter's own declaration."""
        ...

    def arrange_speech(self) -> None:
        """Arrange the next transcription to hear the canonical two-segment speech."""
        ...

    def arrange_silence(self) -> None:
        """Arrange the next transcription to hear nothing at all."""
        ...

    def arrange_error(self, kind: ErrorKind) -> bool:
        """Arrange the next transcription to fail as `kind`; False when it cannot honestly."""
        ...

    def arrange_down(self) -> None:
        """Arrange the next `health()` to find the provider unusable."""
        ...

    def heard(self) -> list[str | None]:
        """Return the language hint of every transcription that reached the model or server."""
        ...


def _canonical_transcript() -> Transcript:
    """Build the transcript every harness's speech arrangement should come back as."""
    segments = [
        TranscriptSegment(start_s=start, end_s=end, text=text, confidence=None)
        for text, start, end, _ in CANONICAL_SEGMENTS
    ]
    return Transcript.from_segments(segments, language=None, duration_s=1.0)


class FakeHarness:
    """Script FakeTranscription directly."""

    def __init__(self) -> None:
        """Start with no provider built."""
        self._fake = FakeTranscription(name="fake")

    def make_provider(
        self, capabilities: TranscriptionCapabilities | None = None
    ) -> TranscriptionProvider:
        """Build a fresh FakeTranscription."""
        self._fake = FakeTranscription(name="fake", capabilities=capabilities)
        return self._fake

    def arrange_speech(self) -> None:
        """Script the canonical transcript."""
        self._fake.script(_canonical_transcript())

    def arrange_silence(self) -> None:
        """Leave the script empty: the fake answers silence spanning the clip."""

    def arrange_error(self, kind: ErrorKind) -> bool:
        """Script the matching typed error."""
        if kind == "rate_limited":
            self._fake.script(RateLimitedError("fake", retry_after_s=RETRY_AFTER_S))
        else:
            self._fake.script(ProviderUnavailableError("fake", "scripted outage"))
        return True

    def arrange_down(self) -> None:
        """Simulate a total outage."""
        self._fake.set_outage(True)

    def heard(self) -> list[str | None]:
        """Return every call's language hint."""
        return [call.language for call in self._fake.calls]


class OpenAICompatHarness:
    """Serve OpenAICompatTranscription from a mock transport replaying the recorded reply."""

    def __init__(self) -> None:
        """Start with an empty reply queue."""
        self._replies: deque[_Reply] = deque()
        self._languages: list[str | None] = []
        self._is_down = False

    def make_provider(
        self, capabilities: TranscriptionCapabilities | None = None
    ) -> TranscriptionProvider:
        """Build a fresh adapter over a mock transport; reset every queued reply."""
        self._replies.clear()
        self._languages = []
        self._is_down = False
        fields: dict[str, object] = {"base_url": _BASE_URL, "model": MODEL, "timeout_s": 5.0}
        if capabilities is not None:
            fields["capabilities"] = capabilities
        config = OpenAICompatTranscriptionConfig(**fields)
        http = httpx.AsyncClient(transport=httpx.MockTransport(self._handle), base_url=_BASE_URL)
        return OpenAICompatTranscription("openai_compat", config, http, FakeClock())

    def arrange_speech(self) -> None:
        """Queue the recorded verbose_json reply."""
        body = (_FIXTURE / "transcription_verbose.json").read_bytes()
        self._replies.append((200, body, {"content-type": "application/json"}))

    def arrange_silence(self) -> None:
        """Queue a reply with no text and no segments."""
        self._replies.append((200, json.dumps(_SILENCE_REPLY).encode(), {}))

    def arrange_error(self, kind: ErrorKind) -> bool:
        """Queue a 429 with a Retry-After, or a 503."""
        if kind == "rate_limited":
            self._replies.append((429, b"{}", {"Retry-After": str(RETRY_AFTER_S)}))
        else:
            self._replies.append((503, b"{}", {}))
        return True

    def arrange_down(self) -> None:
        """Make the health probe's connection fail."""
        self._is_down = True

    def heard(self) -> list[str | None]:
        """Return the language field of every upload the server received."""
        return list(self._languages)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """Route one mock request: the upload, or the /models health probe."""
        if request.url.path == _MODELS_PATH:
            if self._is_down:
                raise httpx.ConnectError("nothing is listening")
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        self._languages.append(_form_field(request.read(), "language"))
        status, body, headers = (
            self._replies.popleft()
            if self._replies
            else (200, json.dumps(_SILENCE_REPLY).encode(), {})
        )
        return httpx.Response(status, content=body, headers=headers)


class WhisperHarness:
    """Hand WhisperLocalTranscription a stand-in model at its own ModelLoader seam."""

    def __init__(self) -> None:
        """Start with a fresh stand-in."""
        self._loader = StandInLoader()

    def make_provider(
        self, capabilities: TranscriptionCapabilities | None = None
    ) -> TranscriptionProvider:
        """Build a fresh adapter over a fresh stand-in loader."""
        self._loader = StandInLoader(StandInWhisperModel())
        config = WhisperConfig(
            model=MODEL, capabilities=capabilities or TranscriptionCapabilities()
        )
        return WhisperLocalTranscription("whisper_local", config, FakeClock(), self._loader)

    def arrange_speech(self) -> None:
        """Give the stand-in the canonical segments, with faster-whisper's leading spaces."""
        self._loader.model.segments = [
            StandInSegment(start=start, end=end, text=f" {text}", avg_logprob=logprob)
            for text, start, end, logprob in CANONICAL_SEGMENTS
        ]

    def arrange_silence(self) -> None:
        """Give the stand-in no segments."""
        self._loader.model.segments = []

    def arrange_error(self, kind: ErrorKind) -> bool:
        """Make the model fail as a device error would; an in-process model has no rate limit."""
        if kind == "rate_limited":
            return False
        self._loader.model.failure = RuntimeError("device lost")
        return True

    def arrange_down(self) -> None:
        """Report the library as not installed."""
        self._loader.missing = "faster-whisper is not installed"

    def heard(self) -> list[str | None]:
        """Return the language hint of every run the stand-in model saw."""
        return [language for _, language in self._loader.model.calls]


def _form_field(body: bytes, name: str) -> str | None:
    """Read one text field's value out of a multipart body, or None when it is absent."""
    marker = f'name="{name}"\r\n\r\n'.encode()
    start = body.find(marker)
    if start < 0:
        return None
    value_start = start + len(marker)
    return body[value_start : body.find(b"\r\n", value_start)].decode()
