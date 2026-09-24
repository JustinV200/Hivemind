"""Tests for hivemind.llm.providers.openai_compat.transcription.provider: the transcriber.

Every test builds an `httpx.AsyncClient` over `httpx.MockTransport` serving the recorded reply
fixtures under `packages/hivemind/tests/fixtures/llm/openai_compat/`; no network is used.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/transcription/provider.py (codingrules
    section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.transcription.provider for the module under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from builders.audio import make_chunks, make_tone_clip, tone_pcm
from pydantic import SecretStr

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.providers.openai_compat.transcription.provider import (
    MODELS_PATH,
    TRANSCRIPTIONS_PATH,
    OpenAICompatTranscription,
    OpenAICompatTranscriptionConfig,
)
from hivemind.llm.transcription import AudioChunk, TranscriptionCapabilities
from waggle.clock import FakeClock

BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; the mock transport answers.
FIXTURES_DIR = Path(__file__).resolve().parents[5] / "fixtures" / "llm" / "openai_compat"
_SECRET = "transcription-probe-value"  # noqa: S105 -- a probe value, never a real credential.


def _config(**overrides: object) -> OpenAICompatTranscriptionConfig:
    """Build a config for a local speech server, with any field overridden."""
    fields: dict[str, object] = {"base_url": BASE_URL, "model": "speech-model", "timeout_s": 5.0}
    fields.update(overrides)
    return OpenAICompatTranscriptionConfig(**fields)


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    config: OpenAICompatTranscriptionConfig | None = None,
) -> OpenAICompatTranscription:
    """Build the transcriber over a mock-transport client."""
    cfg = config if config is not None else _config()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=cfg.base_url)
    return OpenAICompatTranscription("speech", cfg, http, FakeClock())


def _fixture_reply(
    name: str, seen: list[httpx.Request]
) -> Callable[[httpx.Request], httpx.Response]:
    """Answer every request with one recorded reply body, remembering each request."""
    body = (FIXTURES_DIR / name).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return handler


async def _iterate(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` in order."""
    for chunk in chunks:
        yield chunk


async def test_transcribe_uploads_to_the_transcriptions_path_and_maps_the_reply() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(_fixture_reply("transcription_verbose.json", seen))

    transcript = await provider.transcribe(make_tone_clip(), "en")

    assert transcript.text == "Hello from the hive."
    assert transcript.language == "en"
    assert len(transcript.segments) == 2
    assert seen[0].url.path == "/v1" + TRANSCRIPTIONS_PATH
    body = seen[0].read()
    assert b"verbose_json" in body and b'name="language"' in body


async def test_transcribe_accepts_a_plain_json_reply() -> None:
    seen: list[httpx.Request] = []
    clip = make_tone_clip(1.0)

    transcript = await _provider(_fixture_reply("transcription_text.json", seen)).transcribe(clip)

    assert transcript.text == "Hello from the hive."
    assert transcript.segments[0].end_s == clip.duration_s


async def test_transcribe_refuses_an_over_long_clip_without_uploading_it() -> None:
    seen: list[httpx.Request] = []
    config = _config(capabilities=TranscriptionCapabilities(max_clip_s=0.5))
    provider = _provider(_fixture_reply("transcription_text.json", seen), config)

    with pytest.raises(ProviderRequestError):
        await provider.transcribe(make_tone_clip(1.0))
    assert seen == []


async def test_stream_buffers_the_chunks_into_one_upload() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(_fixture_reply("transcription_verbose.json", seen))

    segments = [s async for s in provider.stream(_iterate(make_chunks(tone_pcm(), pieces=6)))]

    assert [segment.text for segment in segments] == ["Hello from", "the hive."]
    assert len(seen) == 1


def test_create_sets_the_bearer_header_only_when_a_key_is_configured() -> None:
    keyed = OpenAICompatTranscription.create(
        "speech", _config(api_key=SecretStr(_SECRET)), FakeClock()
    )
    open_server = OpenAICompatTranscription.create("speech", _config(), FakeClock())

    assert keyed._http.headers["Authorization"] == f"Bearer {_SECRET}"
    assert "Authorization" not in open_server._http.headers
    assert _SECRET not in repr(_config(api_key=SecretStr(_SECRET)))


@pytest.mark.parametrize(
    ("status", "state"),
    [(200, HealthState.HEALTHY), (401, HealthState.DOWN), (404, HealthState.DEGRADED)],
)
async def test_health_reads_the_models_route_status(status: int, state: HealthState) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1" + MODELS_PATH
        return httpx.Response(status, json={"data": []})

    assert (await _provider(handler).health()).state is state


async def test_health_is_down_when_nothing_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert (await _provider(handler).health()).state is HealthState.DOWN
