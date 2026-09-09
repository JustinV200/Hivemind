"""Tests for hivemind.llm.providers.anthropic.provider: AnthropicConfig/AnthropicProvider.

Every test builds an `anthropic.AsyncAnthropic` over the SDK's own mock transport and either
passes it straight to `AnthropicProvider`'s constructor or, for `from_config()` itself, inspects
the client it builds without ever sending a real request over it.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/anthropic/provider.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.anthropic.provider for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import anthropic
import httpx2
import pytest
from builders.llm import make_request
from pydantic import SecretStr

from hivemind.llm.capabilities import HealthState, ProviderCapabilities
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.models import JsonObject, LLMRequest
from hivemind.llm.providers.anthropic.provider import AnthropicConfig, AnthropicProvider
from waggle.clock import FakeClock

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "anthropic"


def _request(**overrides: object) -> LLMRequest:
    fields: dict[str, object] = {"model": "test-model"}
    fields.update(overrides)
    return make_request(**fields)


def _load_fixture(name: str) -> JsonObject:
    payload = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _make_config(**overrides: object) -> AnthropicConfig:
    return AnthropicConfig(**overrides)


def _make_provider(
    handler: Callable[[httpx2.Request], httpx2.Response],
    config: AnthropicConfig | None = None,
    clock: FakeClock | None = None,
) -> AnthropicProvider:
    sdk = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    cfg = config if config is not None else _make_config()
    return AnthropicProvider("p", cfg, sdk, clock if clock is not None else FakeClock())


def _json_handler(status_code: int, body: object) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, json=body)

    return handler


def _capturing_handler(
    status_code: int, body: object, sink: list[JsonObject]
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        sink.append(json.loads(request.content))
        return httpx2.Response(status_code, json=body)

    return handler


# ──────────────────────────────────────────────────────────────────────────────
# AnthropicConfig: secrets and defaults
# ──────────────────────────────────────────────────────────────────────────────


def test_api_key_never_appears_in_repr_or_str() -> None:
    config = _make_config(api_key=SecretStr("supersecret"))

    assert "supersecret" not in repr(config)
    assert "supersecret" not in str(config)


def test_api_key_defaults_to_none() -> None:
    config = _make_config()

    assert config.api_key is None


def test_base_url_defaults_to_none() -> None:
    config = _make_config()

    assert config.base_url is None


def test_capabilities_default_to_full_at_the_declared_context_window() -> None:
    config = _make_config()

    assert config.capabilities == ProviderCapabilities.full(context_window=200_000)


# ──────────────────────────────────────────────────────────────────────────────
# from_config(): the SDK client it builds
# ──────────────────────────────────────────────────────────────────────────────


def test_from_config_never_raises_when_no_api_key_is_configured() -> None:
    config = _make_config(api_key=None)

    provider = AnthropicProvider.from_config("p", config, FakeClock())  # Must not raise.

    # An explicit empty string (never None): the SDK never goes looking for an ambient
    # credential the Hive Manifest did not authorize. See provider.py's docstring.
    # Accessing the private client is the only way to prove this without a live request.
    assert provider._client._sdk.api_key == ""


def test_from_config_sets_the_configured_api_key() -> None:
    config = _make_config(api_key=SecretStr("supersecret"))

    provider = AnthropicProvider.from_config("p", config, FakeClock())

    assert provider._client._sdk.api_key == "supersecret"


def test_from_config_sets_the_configured_timeout() -> None:
    config = _make_config(timeout_s=42.0)

    provider = AnthropicProvider.from_config("p", config, FakeClock())

    assert provider._client._sdk.timeout == 42.0


# ──────────────────────────────────────────────────────────────────────────────
# name / capabilities
# ──────────────────────────────────────────────────────────────────────────────


def test_name_and_capabilities_come_from_construction() -> None:
    capabilities = ProviderCapabilities.full()
    provider = _make_provider(
        _json_handler(200, {}), config=_make_config(capabilities=capabilities)
    )

    assert provider.name == "p"
    assert provider.capabilities == capabilities


# ──────────────────────────────────────────────────────────────────────────────
# complete()
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_maps_a_text_response() -> None:
    fixture = _load_fixture("message_text.json")
    provider = _make_provider(_json_handler(200, fixture))

    response = await provider.complete(_request())

    assert response.text == "Hello there."


async def test_complete_maps_a_tool_call_response() -> None:
    fixture = _load_fixture("message_tool_call.json")
    provider = _make_provider(_json_handler(200, fixture))

    response = await provider.complete(_request())

    assert response.tool_calls[0].name == "get_weather"


async def test_complete_raises_when_request_model_is_none() -> None:
    provider = _make_provider(_json_handler(200, {}))

    with pytest.raises(ProviderRequestError):
        await provider.complete(_request(model=None))


async def test_complete_at_reduced_capabilities_omits_what_they_deny() -> None:
    seen: list[JsonObject] = []
    fixture = _load_fixture("message_text.json")
    provider = _make_provider(
        _capturing_handler(200, fixture, seen),
        config=_make_config(capabilities=ProviderCapabilities.none()),
    )

    await provider.complete(_request())

    body = seen[0]
    assert "thinking" not in body
    assert "output_config" not in body
    assert "tools" not in body


# ──────────────────────────────────────────────────────────────────────────────
# stream()
# ──────────────────────────────────────────────────────────────────────────────


async def test_stream_yields_text_chunks_then_a_final_usage_chunk() -> None:
    body = (FIXTURES_DIR / "stream_text.sse").read_text(encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider = _make_provider(handler)

    chunks = [chunk async for chunk in provider.stream(_request())]

    text = "".join(chunk.text for chunk in chunks if chunk.text is not None)
    assert text == "Hello"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.input_tokens == 10


async def test_stream_yields_a_tool_call_chunk_before_the_final_chunk() -> None:
    body = (FIXTURES_DIR / "stream_tool_call.sse").read_text(encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider = _make_provider(handler)

    chunks = [chunk async for chunk in provider.stream(_request())]

    tool_chunks = [c for c in chunks if c.tool_call is not None]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_call is not None
    assert tool_chunks[0].tool_call.name == "get_weather"
    assert chunks[-1].tool_call is None and chunks[-1].stop_reason is not None


async def test_stream_falls_back_to_one_complete_call_when_streaming_is_false() -> None:
    fixture = _load_fixture("message_text.json")
    capabilities = ProviderCapabilities.full().model_copy(update={"streaming": False})
    provider = _make_provider(
        _json_handler(200, fixture), config=_make_config(capabilities=capabilities)
    )

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert len(chunks) == 1
    assert chunks[0].text == "Hello there."
    assert chunks[0].usage is not None


# ──────────────────────────────────────────────────────────────────────────────
# count_tokens()
# ──────────────────────────────────────────────────────────────────────────────


async def test_count_tokens_returns_none_when_token_counting_is_false() -> None:
    capabilities = ProviderCapabilities.full().model_copy(update={"token_counting": False})
    provider = _make_provider(
        _json_handler(200, {}), config=_make_config(capabilities=capabilities)
    )

    count = await provider.count_tokens(_request())

    assert count is None


async def test_count_tokens_returns_the_api_estimate() -> None:
    provider = _make_provider(_json_handler(200, _load_fixture("count_tokens.json")))

    count = await provider.count_tokens(_request())

    assert count == 42


# ──────────────────────────────────────────────────────────────────────────────
# health()
# ──────────────────────────────────────────────────────────────────────────────


async def test_health_is_healthy_on_200() -> None:
    provider = _make_provider(_json_handler(200, {"data": []}))

    health = await provider.health()

    assert health.state == HealthState.HEALTHY


async def test_health_is_down_on_401() -> None:
    provider = _make_provider(
        _json_handler(
            401, {"type": "error", "error": {"type": "authentication_error", "message": "no key"}}
        )
    )

    health = await provider.health()

    assert health.state == HealthState.DOWN


async def test_health_is_degraded_on_429() -> None:
    provider = _make_provider(
        _json_handler(
            429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
        )
    )

    health = await provider.health()

    assert health.state == HealthState.DEGRADED


async def test_health_reads_the_clock_for_checked_at() -> None:
    clock = FakeClock()
    provider = _make_provider(_json_handler(200, {"data": []}), clock=clock)

    health = await provider.health()

    assert health.checked_at == clock.now()


# ──────────────────────────────────────────────────────────────────────────────
# The API key never leaks
# ──────────────────────────────────────────────────────────────────────────────


async def test_api_key_never_appears_in_an_exception_message_end_to_end() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
        )

    sdk = anthropic.AsyncAnthropic(
        api_key="supersecret-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    provider = AnthropicProvider("p", _make_config(), sdk, FakeClock())

    with pytest.raises(ProviderRequestError) as excinfo:
        await provider.complete(_request())
    assert "supersecret-key" not in str(excinfo.value)
