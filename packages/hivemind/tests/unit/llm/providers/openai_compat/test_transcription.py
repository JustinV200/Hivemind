"""Tests for hivemind.llm.providers.openai_compat.transcription: the transcription wire adapter.

Every test builds an `httpx.AsyncClient` over `httpx.MockTransport` (no network) and passes it
to `OpenAICompatTranscription`'s constructor, or, for `create()` itself, inspects the client it
built, as test_provider.py does for the chat adapter. The protocol-wide clauses (answers, errors,
capabilities, health) live in contracts/test_transcription_provider_contract.py; these cover the
wire shape and what only this adapter does.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/transcription.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.transcription for the module under test.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path

import httpx
import pytest
from builders.audio import make_clip
from pydantic import JsonValue, SecretStr

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError
from hivemind.llm.providers.openai_compat import (
    OpenAICompatTranscription,
    OpenAICompatTranscriptionConfig,
)
from hivemind.llm.providers.openai_compat.transcription import CONNECT_TIMEOUT_S
from hivemind.llm.transcription import AudioClip, AudioMediaType
from waggle.clock import FakeClock

BASE_URL = "http://127.0.0.1:9/v1"
FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "openai_compat"
_OPUS_BYTES = b"\x1aE\xdf\xa3" + b"\x00" * 60  # A WebM magic number, then opaque bytes.

# What httpx.MockTransport accepts: a plain handler, or an async one (to simulate a hang).
Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


def _verbose_body() -> JsonValue:
    """Return the recorded verbose_json answer."""
    parsed: JsonValue = json.loads(
        (FIXTURES_DIR / "transcription_verbose.json").read_text(encoding="utf-8")
    )
    return parsed


def _config(**overrides: object) -> OpenAICompatTranscriptionConfig:
    """Build a config with neutral defaults."""
    fields: dict[str, object] = {"base_url": BASE_URL, "model": "test-model", "timeout_s": 5.0}
    fields.update(overrides)
    return OpenAICompatTranscriptionConfig.model_validate(fields)


def _provider(
    handler: Handler, config: OpenAICompatTranscriptionConfig | None = None
) -> OpenAICompatTranscription:
    """Build the adapter over a mock transport routed to `handler`."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return OpenAICompatTranscription("p", config or _config(), http, FakeClock())


class _Recorder:
    """A handler that answers every upload with `body` and keeps the requests it saw."""

    def __init__(self, body: object, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._body = body
        self._status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json=self._body)


# ──────────────────────────────────────────────────────────────────────────────
# The upload's wire shape
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_upload_is_a_multipart_form_naming_the_model_and_verbose_format() -> None:
    recorder = _Recorder(_verbose_body())
    clip = make_clip()

    await _provider(recorder).transcribe(clip)

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/audio/transcriptions"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="model"\r\n\r\ntest-model\r\n' in request.content
    assert b'name="response_format"\r\n\r\nverbose_json\r\n' in request.content
    assert b'filename="clip.wav"' in request.content
    assert b"Content-Type: audio/wav" in request.content
    assert clip.data in request.content
    assert b'name="language"' not in request.content


async def test_a_language_hint_travels_as_its_primary_subtag() -> None:
    recorder = _Recorder(_verbose_body())

    await _provider(recorder).transcribe(make_clip(), "pt-BR")

    assert b'name="language"\r\n\r\npt\r\n' in recorder.requests[0].content


async def test_a_compressed_clip_is_named_with_its_formats_extension() -> None:
    recorder = _Recorder({"text": "hello"})
    clip = AudioClip.from_upload(_OPUS_BYTES, "audio/webm;codecs=opus", duration_s=1.0)

    await _provider(recorder).transcribe(clip)

    assert b'filename="clip.webm"' in recorder.requests[0].content
    assert b"Content-Type: audio/webm" in recorder.requests[0].content


# ──────────────────────────────────────────────────────────────────────────────
# Reading the answer
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_servers_measured_duration_wins_over_the_clips_claim() -> None:
    clip = make_clip(2.0)

    transcript = await _provider(_Recorder(_verbose_body())).transcribe(clip)

    assert transcript.duration_s == 1.0


@pytest.mark.parametrize("duration", [True, -1, "1.0", None])
async def test_a_duration_that_is_not_a_non_negative_number_falls_back_to_the_clips(
    duration: JsonValue,
) -> None:
    clip = make_clip(2.0)

    transcript = await _provider(_Recorder({"text": "x", "duration": duration})).transcribe(clip)

    assert transcript.duration_s == clip.duration_s


async def test_a_language_name_longer_than_a_language_is_dropped() -> None:
    body = {"text": "x", "language": "a" * 40}

    transcript = await _provider(_Recorder(body)).transcribe(make_clip())

    assert transcript.language is None


@pytest.mark.parametrize(
    "body",
    [
        {"segments": []},
        {"text": 42},
        {"text": "x", "segments": [{"start": 1.0, "end": 0.5, "text": "backwards"}]},
        {"text": "x", "segments": ["not an object"]},
        {"text": "x" * 100_001},
    ],
)
async def test_an_answer_that_does_not_fit_a_transcript_is_a_request_error(
    body: JsonValue,
) -> None:
    with pytest.raises(ProviderRequestError):
        await _provider(_Recorder(body)).transcribe(make_clip())


async def test_a_plain_text_body_is_a_request_error_not_a_raw_value_error() -> None:
    provider = _provider(lambda _request: httpx.Response(200, text="Turn on the lights."))

    with pytest.raises(ProviderRequestError, match="not JSON"):
        await provider.transcribe(make_clip())


# ──────────────────────────────────────────────────────────────────────────────
# Unavailability and timeouts
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_refused_connection_is_provider_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderUnavailableError):
        await _provider(refuse).transcribe(make_clip())


async def test_a_server_that_never_answers_is_abandoned_after_the_whole_call_timeout() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        return httpx.Response(200)  # Never reached.

    provider = _provider(hang, _config(timeout_s=0.01))

    with pytest.raises(ProviderUnavailableError, match="no transcript within"):
        await provider.transcribe(make_clip())


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_health_reports_down_when_the_server_cannot_be_reached(
    error: type[httpx.TransportError],
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise error("unreachable", request=request)

    health = await _provider(fail).health()

    assert health.state is HealthState.DOWN


# ──────────────────────────────────────────────────────────────────────────────
# create() and aclose(): the client it builds, and releases
# ──────────────────────────────────────────────────────────────────────────────


async def test_create_sends_the_api_key_as_a_bearer_token() -> None:
    provider = OpenAICompatTranscription.create(
        "p", _config(api_key=SecretStr("supersecret")), FakeClock()
    )

    # create()'s whole job is building this client, so this is what testing create() means.
    assert provider._http.headers["authorization"] == "Bearer supersecret"
    assert "supersecret" not in repr(_config(api_key=SecretStr("supersecret")))
    await provider.aclose()


async def test_create_without_a_key_sends_no_authorization_and_bounds_both_phases() -> None:
    provider = OpenAICompatTranscription.create("p", _config(timeout_s=42.0), FakeClock())

    assert "authorization" not in provider._http.headers
    assert provider._http.timeout.connect == CONNECT_TIMEOUT_S
    assert provider._http.timeout.read == 42.0
    await provider.aclose()


async def test_aclose_closes_the_client_it_was_given() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)), base_url=BASE_URL
    )
    provider = OpenAICompatTranscription("p", _config(), http, FakeClock())

    await provider.aclose()

    assert http.is_closed


async def test_the_clip_bytes_never_appear_in_a_request_error() -> None:
    clip = AudioClip.from_upload(b"private-words" + _OPUS_BYTES, AudioMediaType.MP3, 1.0)
    body = {"error": {"message": "invalid file format", "type": "invalid_request_error"}}

    with pytest.raises(ProviderRequestError) as excinfo:
        await _provider(_Recorder(body, status=400)).transcribe(clip)

    assert "private-words" not in str(excinfo.value)
