"""Tests for hivemind.llm.providers.anthropic.client: AnthropicClient and map_error, in isolation.

Every test builds an `anthropic.AsyncAnthropic` over the SDK's own mock transport
(`anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))`, per codingrules
14.3: "no network in CI") -- never `httpx.MockTransport`, since `anthropic` 1.x is built on the
`httpx2` fork -- and drives `AnthropicClient` against it.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/anthropic/client.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.anthropic.client for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import anthropic
import httpx2
import pytest

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import (
    ContextTooLongError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from hivemind.llm.models import JsonObject
from hivemind.llm.providers.anthropic.client import AnthropicClient, map_error
from waggle.clock import FakeClock

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "anthropic"
BASE_PARAMS: JsonObject = {
    "model": "test-model",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
}
API_KEY = "supersecret-test-key"  # Never a real key; only ever sent to the mock transport below.


def _load_fixture(name: str) -> JsonObject:
    payload = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _make_sdk(handler: Callable[[httpx2.Request], httpx2.Response]) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=API_KEY,
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def _make_client(
    handler: Callable[[httpx2.Request], httpx2.Response], context_window: int = 200_000
) -> AnthropicClient:
    return AnthropicClient(_make_sdk(handler), "p", context_window)


def _json_handler(status_code: int, body: object) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, json=body)

    return handler


def _error_body(error_type: str, message: str) -> JsonObject:
    return {"type": "error", "error": {"type": error_type, "message": message}}


# ──────────────────────────────────────────────────────────────────────────────
# create(): success and every error mapping
# ──────────────────────────────────────────────────────────────────────────────


async def test_create_returns_the_parsed_message() -> None:
    client = _make_client(_json_handler(200, _load_fixture("message_text.json")))

    message = await client.create(BASE_PARAMS)

    assert message.content[0].text == "Hello there."  # type: ignore[union-attr]


async def test_create_maps_429_with_retry_after_header() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429,
            json=_error_body("rate_limit_error", "slow down"),
            headers={"retry-after": "3"},
        )

    client = _make_client(handler)

    with pytest.raises(RateLimitedError) as excinfo:
        await client.create(BASE_PARAMS)
    assert excinfo.value.retry_after_s == 3.0


async def test_create_maps_429_without_retry_after_to_no_retry_hint() -> None:
    client = _make_client(_json_handler(429, _error_body("rate_limit_error", "slow down")))

    with pytest.raises(RateLimitedError) as excinfo:
        await client.create(BASE_PARAMS)
    assert excinfo.value.retry_after_s is None


async def test_create_maps_a_connection_failure_to_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    client = _make_client(handler)

    with pytest.raises(ProviderUnavailableError):
        await client.create(BASE_PARAMS)


async def test_create_maps_5xx_to_unavailable() -> None:
    client = _make_client(_json_handler(500, _error_body("api_error", "boom")))

    with pytest.raises(ProviderUnavailableError):
        await client.create(BASE_PARAMS)


async def test_create_maps_529_overloaded_to_unavailable() -> None:
    # 529 is OverloadedError in this SDK version, a sibling of InternalServerError rather than a
    # subclass of it -- map_error's `status_code >= 500` check is what actually catches it.
    client = _make_client(_json_handler(529, _error_body("overloaded_error", "overloaded")))

    with pytest.raises(ProviderUnavailableError):
        await client.create(BASE_PARAMS)


async def test_create_maps_a_context_length_400_to_context_too_long() -> None:
    body = _load_fixture("error_context_length.json")
    client = _make_client(_json_handler(400, body), context_window=8_192)

    with pytest.raises(ContextTooLongError) as excinfo:
        await client.create(BASE_PARAMS)
    assert excinfo.value.window == 8_192


async def test_create_maps_a_plain_400_to_provider_request_error() -> None:
    client = _make_client(_json_handler(400, _error_body("invalid_request_error", "bad field")))

    with pytest.raises(ProviderRequestError) as excinfo:
        await client.create(BASE_PARAMS)
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_type == "invalid_request_error"


async def test_create_maps_401_to_provider_request_error() -> None:
    client = _make_client(_json_handler(401, _error_body("authentication_error", "no key")))

    with pytest.raises(ProviderRequestError) as excinfo:
        await client.create(BASE_PARAMS)
    assert excinfo.value.status_code == 401


async def test_api_key_never_appears_in_a_mapped_errors_message() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["x-api-key"] == API_KEY  # Proves the key really was sent.
        return httpx2.Response(400, json=_error_body("invalid_request_error", "bad field"))

    client = _make_client(handler)

    with pytest.raises(ProviderRequestError) as excinfo:
        await client.create(BASE_PARAMS)
    assert API_KEY not in str(excinfo.value)


# ──────────────────────────────────────────────────────────────────────────────
# stream(): events then the final message, and error mapping mid-stream
# ──────────────────────────────────────────────────────────────────────────────


async def test_stream_yields_events_then_the_final_message_last() -> None:
    body = (FIXTURES_DIR / "stream_text.sse").read_text(encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = _make_client(handler)

    items = [item async for item in client.stream(BASE_PARAMS)]

    assert isinstance(items[-1], anthropic.types.Message)
    assert items[-1].content[0].text == "Hello"  # type: ignore[union-attr]
    # Every earlier item is a raw/parsed stream event, never the final Message.
    assert all(not isinstance(item, anthropic.types.Message) for item in items[:-1])


async def test_stream_maps_a_connection_failure_to_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    client = _make_client(handler)

    with pytest.raises(ProviderUnavailableError):
        async for _ in client.stream(BASE_PARAMS):
            pass


async def test_stream_maps_a_429_before_any_event() -> None:
    client = _make_client(_json_handler(429, _error_body("rate_limit_error", "slow down")))

    with pytest.raises(RateLimitedError):
        async for _ in client.stream(BASE_PARAMS):
            pass


# ──────────────────────────────────────────────────────────────────────────────
# count_tokens()
# ──────────────────────────────────────────────────────────────────────────────


async def test_count_tokens_returns_the_api_estimate() -> None:
    client = _make_client(_json_handler(200, _load_fixture("count_tokens.json")))

    count = await client.count_tokens({"model": "test-model", "messages": BASE_PARAMS["messages"]})

    assert count == 42


async def test_count_tokens_maps_errors_like_create() -> None:
    client = _make_client(_json_handler(500, _error_body("api_error", "boom")))

    with pytest.raises(ProviderUnavailableError):
        await client.count_tokens({"model": "test-model", "messages": BASE_PARAMS["messages"]})


# ──────────────────────────────────────────────────────────────────────────────
# probe_health()
# ──────────────────────────────────────────────────────────────────────────────


async def test_probe_health_is_healthy_on_200() -> None:
    client = _make_client(_json_handler(200, {"data": []}))

    health = await client.probe_health(FakeClock())

    assert health.state == HealthState.HEALTHY


async def test_probe_health_is_degraded_on_429() -> None:
    client = _make_client(_json_handler(429, _error_body("rate_limit_error", "slow down")))

    health = await client.probe_health(FakeClock())

    assert health.state == HealthState.DEGRADED


async def test_probe_health_is_degraded_on_5xx() -> None:
    client = _make_client(_json_handler(500, _error_body("api_error", "down")))

    health = await client.probe_health(FakeClock())

    assert health.state == HealthState.DEGRADED


async def test_probe_health_is_down_on_401() -> None:
    client = _make_client(_json_handler(401, _error_body("authentication_error", "no key")))

    health = await client.probe_health(FakeClock())

    assert health.state == HealthState.DOWN


async def test_probe_health_is_down_on_connection_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    client = _make_client(handler)

    health = await client.probe_health(FakeClock())

    assert health.state == HealthState.DOWN


async def test_probe_health_reads_the_clock_for_checked_at() -> None:
    clock = FakeClock()
    client = _make_client(_json_handler(200, {"data": []}))

    health = await client.probe_health(clock)

    assert health.checked_at == clock.now()


async def test_probe_health_never_raises_on_an_error_status() -> None:
    client = _make_client(_json_handler(403, _error_body("permission_error", "forbidden")))

    health = await client.probe_health(FakeClock())  # Must not raise.

    assert health.state == HealthState.DOWN


# ──────────────────────────────────────────────────────────────────────────────
# map_error(): the bare function, directly
# ──────────────────────────────────────────────────────────────────────────────


def test_map_error_falls_back_to_provider_request_error_for_an_unrecognised_api_error() -> None:
    request = httpx2.Request("POST", "https://example.invalid/v1/messages")
    exc = anthropic.APIError("weird failure", request, body=None)

    mapped = map_error(exc, "p", context_window=8_192)

    assert isinstance(mapped, ProviderRequestError)
    assert mapped.status_code == 0
    assert mapped.provider == "p"
