"""Tests for hivemind.llm.providers.openai_compat.transcription.client: multipart and errors.

Every test builds an `httpx.AsyncClient` over `httpx.MockTransport`; no network is used.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/transcription/client.py (codingrules
    section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.transcription.client for the module under test.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError, RateLimitedError
from hivemind.llm.providers.openai_compat.transcription.client import (
    MAX_ERROR_DETAIL_CHARS,
    TranscriptionClient,
)

BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; the mock transport answers.
PATH = "/audio/transcriptions"
_FORM = {"model": "speech-model"}
_FILES = {"file": ("clip.wav", b"RIFF-bytes", "audio/wav")}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> TranscriptionClient:
    """Wrap a mock-transport httpx client the way the provider wraps a real one."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return TranscriptionClient(http, "speech")


def _answer(status: int, **kwargs: object) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler that answers every request with `status` and `kwargs`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, **kwargs)  # type: ignore[arg-type]  # Test-only passthrough.

    return handler


async def test_post_multipart_sends_the_form_and_the_file_and_parses_the_reply() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "ok"})

    reply = await _client(handler).post_multipart(PATH, _FORM, _FILES)

    assert reply == {"text": "ok"}
    body = seen[0].read()
    assert seen[0].url.path == "/v1" + PATH
    assert seen[0].headers["content-type"].startswith("multipart/form-data")
    assert b'name="model"' in body and b"speech-model" in body
    assert b'filename="clip.wav"' in body and b"RIFF-bytes" in body


async def test_a_server_error_is_unavailable() -> None:
    with pytest.raises(ProviderUnavailableError, match="HTTP 503"):
        await _client(_answer(503)).post_multipart(PATH, _FORM, _FILES)


async def test_a_rate_limit_carries_the_retry_after_hint() -> None:
    handler = _answer(429, headers={"Retry-After": "7"})

    with pytest.raises(RateLimitedError) as caught:
        await _client(handler).post_multipart(PATH, _FORM, _FILES)

    assert caught.value.retry_after_s == 7.0


async def test_a_rate_limit_with_a_date_retry_after_carries_no_hint() -> None:
    handler = _answer(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    with pytest.raises(RateLimitedError) as caught:
        await _client(handler).post_multipart(PATH, _FORM, _FILES)

    assert caught.value.retry_after_s is None


async def test_a_refused_request_carries_the_servers_error_type_and_message() -> None:
    body = {"error": {"message": "Audio file is too short.", "type": "invalid_request_error"}}

    with pytest.raises(ProviderRequestError) as caught:
        await _client(_answer(400, json=body)).post_multipart(PATH, _FORM, _FILES)

    assert caught.value.status_code == 400
    assert caught.value.error_type == "invalid_request_error"
    assert "too short" in str(caught.value)


async def test_a_refused_request_with_a_plain_body_is_clipped() -> None:
    handler = _answer(413, text="x" * (MAX_ERROR_DETAIL_CHARS * 3))

    with pytest.raises(ProviderRequestError) as caught:
        await _client(handler).post_multipart(PATH, _FORM, _FILES)

    assert caught.value.error_type is None
    assert len(str(caught.value)) < MAX_ERROR_DETAIL_CHARS * 2


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"])
async def test_a_success_whose_body_is_not_one_json_object_is_refused(body: bytes) -> None:
    with pytest.raises(ProviderRequestError, match="not a JSON object"):
        await _client(_answer(200, content=body)).post_multipart(PATH, _FORM, _FILES)


@pytest.mark.parametrize(
    "failure", [httpx.ConnectError("refused"), httpx.WriteTimeout("slow upload")]
)
async def test_a_transport_failure_is_unavailable(failure: httpx.TransportError) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(ProviderUnavailableError):
        await _client(handler).post_multipart(PATH, _FORM, _FILES)
