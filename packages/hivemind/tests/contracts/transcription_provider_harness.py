"""Provide one TranscriptionHarness per TranscriptionProvider, for the transcription contract suite.

`test_transcription_provider_contract.py` writes each clause of the `TranscriptionProvider`
contract once and runs it over every implementation registered here: the fake (`FakeHarness`,
scripting `FakeTranscription` directly) and the OpenAI-compatible adapter
(`OpenAICompatHarness`, over `httpx.MockTransport`, answering from the recorded `verbose_json` and
plain `json` bodies under `tests/fixtures/llm/openai_compat/`). No network call is ever made. Both
harnesses arrange the same words (`SPOKEN_TEXT`, `SPOKEN_SEGMENTS`), so a clause asserts one
expected transcript whichever provider produced it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used only by
    `contracts.test_transcription_provider_contract`.

Key invariants:
    - `make_provider()` resets every piece of harness state, so one module-level harness is safe
      to reuse across parametrised tests (the `llm_provider_harness` pattern).
    - Model ids are the neutral `TEST_MODEL` (`scripts/check_no_model_ids.py`).

See Also:
    - contracts.llm_provider_harness for the chat counterpart this mirrors.
    - hivemind.llm.transcription.provider for the protocol every harness builds.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import JsonValue, SecretStr

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import (
    LLMError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from hivemind.llm.providers.openai_compat import (
    OpenAICompatTranscription,
    OpenAICompatTranscriptionConfig,
)
from hivemind.llm.transcription import (
    FakeTranscription,
    Transcript,
    TranscriptionCapabilities,
    TranscriptionProvider,
    TranscriptSegment,
)
from waggle.clock import FakeClock

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "llm" / "openai_compat"
SPOKEN_TEXT = "Turn on the porch lights."  # What both recorded bodies (and the fake) say.
SPOKEN_SEGMENTS = ((0.0, 0.6, "Turn on"), (0.6, 1.0, "the porch lights."))  # verbose_json's.
DETECTED_LANGUAGE = "english"  # The language the recorded verbose body names.
SPOKEN_DURATION_S = 1.0  # The recorded verbose body's duration; every test clip is this long.
RETRY_AFTER_S = 2.0  # What every harness's "rate_limited" arrangement reports.
TEST_MODEL = "test-model"  # Neutral id, never a real provider's (codingrules 8.6).

_BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; never a real server.
_TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
_MODELS_PATH = "/v1/models"

ErrorKind = Literal["rate_limited", "unavailable", "bad_request"]

# One (status, body, headers) wire reply, queued FIFO for the next upload.
_Reply = tuple[int, JsonValue, dict[str, str]]

__all__ = [
    "DETECTED_LANGUAGE",
    "RETRY_AFTER_S",
    "SPOKEN_DURATION_S",
    "SPOKEN_SEGMENTS",
    "SPOKEN_TEXT",
    "TEST_MODEL",
    "ErrorKind",
    "FakeHarness",
    "OpenAICompatHarness",
    "TranscriptionHarness",
]


class TranscriptionHarness(Protocol):
    """Build one TranscriptionProvider implementation and arrange its next answer."""

    def make_provider(
        self, capabilities: TranscriptionCapabilities | None = None, api_key: str | None = None
    ) -> TranscriptionProvider:
        """Build a fresh provider; `capabilities` defaults to `TranscriptionCapabilities.full()`."""
        ...

    def arrange_answer(self, *, verbose: bool = True) -> None:
        """Arrange the next upload's answer: timed segments when `verbose`, text only otherwise."""
        ...

    def arrange_error(self, kind: ErrorKind) -> None:
        """Arrange the next upload to fail the way `kind` names."""
        ...

    def arrange_health(self, state: HealthState) -> None:
        """Arrange every later `health()` call to report `state`."""
        ...

    def supported_health_states(self) -> frozenset[HealthState]:
        """Return the states this harness can honestly arrange."""
        ...

    def uploads(self) -> int:
        """Return how many clips actually reached the provider's transcription since built."""
        ...


class FakeHarness:
    """Script `FakeTranscription` with the same words the recorded wire bodies carry."""

    def __init__(self) -> None:
        """Start with a default fake; every test builds its own via `make_provider()`."""
        self._fake = FakeTranscription()

    def make_provider(
        self, capabilities: TranscriptionCapabilities | None = None, api_key: str | None = None
    ) -> TranscriptionProvider:
        """Build a fresh FakeTranscription; the fake holds no key, so `api_key` is unused."""
        self._fake = FakeTranscription(capabilities=capabilities)
        return self._fake

    def arrange_answer(self, *, verbose: bool = True) -> None:
        """Script the transcript a verbose or plain wire answer maps to."""
        segments = tuple(
            TranscriptSegment(start_s=start, end_s=end, text=text)
            for start, end, text in SPOKEN_SEGMENTS
        )
        self._fake.script(
            Transcript(
                text=SPOKEN_TEXT,
                language=DETECTED_LANGUAGE if verbose else None,
                duration_s=SPOKEN_DURATION_S,
                segments=segments if verbose else (),
            )
        )

    def arrange_error(self, kind: ErrorKind) -> None:
        """Script the typed error the adapter maps `kind`'s wire reply to."""
        errors: dict[ErrorKind, LLMError] = {
            "rate_limited": RateLimitedError("fake", retry_after_s=RETRY_AFTER_S),
            "unavailable": ProviderUnavailableError("fake", "HTTP 503"),
            "bad_request": ProviderRequestError("fake", 400, detail="invalid file format"),
        }
        self._fake.script(errors[kind])

    def arrange_health(self, state: HealthState) -> None:
        """Simulate an outage for DOWN; the fake has no DEGRADED shape to offer."""
        self._fake.set_outage(state is HealthState.DOWN)

    def supported_health_states(self) -> frozenset[HealthState]:
        """HEALTHY and DOWN: the fake is up or out, never half-way."""
        return frozenset({HealthState.HEALTHY, HealthState.DOWN})

    def uploads(self) -> int:
        """Return how many clips the fake recorded as transcribed."""
        return len(self._fake.calls)


class OpenAICompatHarness:
    """Build OpenAICompatTranscription over `httpx.MockTransport`, answering recorded bodies."""

    def __init__(self) -> None:
        """Start empty; `make_provider()` resets everything."""
        self._replies: deque[_Reply] = deque()
        self._health_status = 200
        self._uploads: list[httpx.Request] = []

    def make_provider(
        self, capabilities: TranscriptionCapabilities | None = None, api_key: str | None = None
    ) -> TranscriptionProvider:
        """Build a fresh adapter over a fresh mock transport with nothing queued."""
        self._replies.clear()
        self._health_status = 200
        self._uploads = []
        config = OpenAICompatTranscriptionConfig(
            base_url=_BASE_URL,
            model=TEST_MODEL,
            api_key=SecretStr(api_key) if api_key is not None else None,
            timeout_s=5.0,
            capabilities=capabilities or TranscriptionCapabilities.full(),
        )
        # The header create() would add from the key; built by hand since create() owns its
        # own client and this harness needs the mock transport underneath.
        headers = {"Authorization": f"Bearer {api_key}"} if api_key is not None else {}
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), base_url=_BASE_URL, headers=headers
        )
        return OpenAICompatTranscription("openai_compat", config, http, FakeClock())

    def arrange_answer(self, *, verbose: bool = True) -> None:
        """Queue the recorded verbose_json or plain json body."""
        name = "transcription_verbose.json" if verbose else "transcription_plain.json"
        body: JsonValue = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
        self._replies.append((200, body, {}))

    def arrange_error(self, kind: ErrorKind) -> None:
        """Queue the error reply a real server sends for `kind`."""
        replies: dict[ErrorKind, _Reply] = {
            "rate_limited": (
                429,
                {"error": {"message": "slow down", "type": "rate_limit"}},
                {"Retry-After": str(RETRY_AFTER_S)},
            ),
            "unavailable": (503, {"error": {"message": "overloaded"}}, {}),
            "bad_request": (
                400,
                {"error": {"message": "invalid file format", "type": "invalid_request_error"}},
                {},
            ),
        }
        self._replies.append(replies[kind])

    def arrange_health(self, state: HealthState) -> None:
        """Answer `GET /models` with the status the adapter maps to `state`."""
        statuses = {HealthState.HEALTHY: 200, HealthState.DEGRADED: 503, HealthState.DOWN: 401}
        self._health_status = statuses[state]

    def supported_health_states(self) -> frozenset[HealthState]:
        """All three: the adapter maps a status to each."""
        return frozenset(HealthState)

    def uploads(self) -> int:
        """Return how many transcription requests reached the mock server."""
        return len(self._uploads)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """Route the mock server: the model listing, or the next queued transcription answer."""
        if request.url.path == _MODELS_PATH:
            return httpx.Response(self._health_status, json={"object": "list", "data": []})
        if request.url.path == _TRANSCRIPTIONS_PATH:
            self._uploads.append(request)
            status, body, headers = self._replies.popleft()
            return httpx.Response(status, json=body, headers=headers)
        return httpx.Response(404)
