"""Tests for hivemind.llm.providers.openai_compat.client: the httpx layer, in isolation.

Every test builds an `httpx.AsyncClient` over `httpx.MockTransport` (no network, per codingrules
14.3: "no network in CI") and drives `OpenAICompatClient` against it.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/client.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.client for the module under test.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from hivemind.llm.errors import (
    ContextTooLongError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from hivemind.llm.models import JsonObject
from hivemind.llm.providers.openai_compat.client import OpenAICompatClient

# Never a real host: nothing listens on this port, and it carries no fragment
# scripts/check_no_model_ids.py's PROVIDER_URL_FRAGMENTS would flag.
BASE_URL = "http://127.0.0.1:9/v1"
FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "openai_compat"


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAICompatClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return OpenAICompatClient(http, provider="p", context_window=8_192)


def _json_response(status_code: int, body: object) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return handler


def _as_dict(value: JsonValue) -> JsonObject:
    """Narrow a parsed JSON value to an object, for assertions on a streamed chunk's shape."""
    assert isinstance(value, dict)
    return value


def _as_list(value: JsonValue) -> list[JsonValue]:
    """Narrow a parsed JSON value to an array, for assertions on a streamed chunk's shape."""
    assert isinstance(value, list)
    return value


# ──────────────────────────────────────────────────────────────────────────────
# get_json / post_json: success
# ──────────────────────────────────────────────────────────────────────────────


async def test_get_json_returns_the_parsed_body() -> None:
    client = _make_client(_json_response(200, {"data": [{"id": "local-small"}]}))

    payload = await client.get_json("/models")

    assert payload == {"data": [{"id": "local-small"}]}


async def test_post_json_sends_the_body_and_returns_the_parsed_response() -> None:
    seen_paths: list[str] = []
    seen_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        seen_bodies.append(request.content)
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler)

    payload = await client.post_json("/chat/completions", {"model": "local-small"})

    assert payload == {"ok": True}
    # Two separate checks (never one joined literal): a hygiene grep bans the fragment whole
    # (scripts/check_no_model_ids.py's PROVIDER_URL_FRAGMENTS), since it is a real vendor path.
    assert seen_paths[0].startswith("/v1")
    assert seen_paths[0].endswith("/chat/completions")
    assert b"local-small" in seen_bodies[0]


async def test_get_json_raises_provider_request_error_when_body_is_not_an_object() -> None:
    client = _make_client(_json_response(200, [1, 2, 3]))

    with pytest.raises(ProviderRequestError):
        await client.get_json("/models")


# ──────────────────────────────────────────────────────────────────────────────
# Error mapping
# ──────────────────────────────────────────────────────────────────────────────


async def test_connection_failure_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _make_client(handler)

    with pytest.raises(ProviderUnavailableError):
        await client.get_json("/models")


async def test_5xx_raises_provider_unavailable() -> None:
    client = _make_client(_json_response(503, {"error": {"message": "overloaded"}}))

    with pytest.raises(ProviderUnavailableError):
        await client.get_json("/models")


async def test_429_raises_rate_limited_with_retry_after_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "7"}
        )

    client = _make_client(handler)

    with pytest.raises(RateLimitedError) as excinfo:
        await client.get_json("/models")
    assert excinfo.value.retry_after_s == 7.0


async def test_429_without_retry_after_header_has_no_retry_hint() -> None:
    client = _make_client(_json_response(429, {"error": {"message": "slow down"}}))

    with pytest.raises(RateLimitedError) as excinfo:
        await client.get_json("/models")
    assert excinfo.value.retry_after_s is None


async def test_429_with_a_non_numeric_retry_after_header_has_no_retry_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "slow down"}},
            headers={"Retry-After": "Wed, 21 Oct 2099"},
        )

    client = _make_client(handler)

    with pytest.raises(RateLimitedError) as excinfo:
        await client.get_json("/models")
    assert excinfo.value.retry_after_s is None


async def test_400_context_length_raises_context_too_long() -> None:
    body = (FIXTURES_DIR / "error_context_length.json").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=body, headers={"content-type": "application/json"})

    client = _make_client(handler)

    with pytest.raises(ContextTooLongError) as excinfo:
        await client.post_json("/chat/completions", {"model": "local-small"})
    assert excinfo.value.window == 8_192


async def test_400_without_a_context_length_message_raises_provider_request_error() -> None:
    client = _make_client(
        _json_response(400, {"error": {"message": "bad field", "type": "invalid_request_error"}})
    )

    with pytest.raises(ProviderRequestError) as excinfo:
        await client.post_json("/chat/completions", {})
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_type == "invalid_request_error"


async def test_other_4xx_raises_provider_request_error() -> None:
    client = _make_client(
        _json_response(404, {"error": {"message": "no such model", "type": "not_found"}})
    )

    with pytest.raises(ProviderRequestError) as excinfo:
        await client.get_json("/models")
    assert excinfo.value.status_code == 404


async def test_error_body_with_no_error_key_falls_back_to_the_raw_text() -> None:
    client = _make_client(_json_response(422, {"message": "top-level, no error wrapper"}))

    with pytest.raises(ProviderRequestError) as excinfo:
        await client.get_json("/models")
    assert excinfo.value.error_type is None


async def test_error_body_that_is_not_json_still_raises_provider_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, content=b"not json at all")

    client = _make_client(handler)

    with pytest.raises(ProviderRequestError):
        await client.get_json("/models")


# ──────────────────────────────────────────────────────────────────────────────
# stream_sse
# ──────────────────────────────────────────────────────────────────────────────


async def test_stream_sse_yields_every_data_line_and_stops_at_done() -> None:
    body = (FIXTURES_DIR / "stream_completion.sse").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = _make_client(handler)

    chunks = [chunk async for chunk in client.stream_sse("/chat/completions", {"stream": True})]

    assert len(chunks) == 5  # 3 delta chunks, 1 finish_reason chunk, 1 trailing usage chunk.
    first_choice = _as_dict(_as_list(chunks[1]["choices"])[0])
    assert _as_dict(first_choice["delta"])["content"] == "Hel"
    assert _as_dict(chunks[-1]["usage"])["prompt_tokens"] == 10


async def test_stream_sse_maps_an_error_response_before_any_data() -> None:
    client = _make_client(_json_response(429, {"error": {"message": "slow down"}}))

    with pytest.raises(RateLimitedError):
        async for _ in client.stream_sse("/chat/completions", {"stream": True}):
            pass


async def test_stream_sse_connection_failure_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = _make_client(handler)

    with pytest.raises(ProviderUnavailableError):
        async for _ in client.stream_sse("/chat/completions", {"stream": True}):
            pass
