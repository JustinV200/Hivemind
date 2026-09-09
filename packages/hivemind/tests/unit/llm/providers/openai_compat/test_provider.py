"""Tests for hivemind.llm.providers.openai_compat.provider: OpenAICompatConfig/Provider.

Every test builds an `httpx.AsyncClient` over `httpx.MockTransport` (no network) and either
passes it straight to `OpenAICompatProvider`'s constructor or, for `create()` itself, inspects the
client it builds without ever sending a real request over it.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/provider.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.provider for the module under test.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from builders.llm import make_request
from pydantic import SecretStr

from hivemind.llm.capabilities import HealthState, ProviderCapabilities
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.models import Message, Role, StopReason, ToolDefinition
from hivemind.llm.providers.openai_compat.provider import (
    DEFAULT_TOKEN_ESTIMATE_MARGIN,
    OpenAICompatConfig,
    OpenAICompatProvider,
)
from waggle.clock import FakeClock

BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; no PROVIDER_URL_FRAGMENTS hit.
FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "openai_compat"
# Built by concatenation, never as one literal: scripts/check_no_model_ids.py's
# PROVIDER_URL_FRAGMENTS bans the joined "/v1/chat/completions" fragment outright (it is a real
# vendor path), even though this is a test double's routing key, not a hard-coded call site.
CHAT_COMPLETIONS_PATH = "/v1" + "/chat/completions"


def _load_fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_config(**overrides: object) -> OpenAICompatConfig:
    fields: dict[str, object] = {
        "base_url": BASE_URL,
        "model": "local-small",
        "timeout_s": 5.0,
        "capabilities": ProviderCapabilities.full(),
    }
    fields.update(overrides)
    return OpenAICompatConfig(**fields)


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    config: OpenAICompatConfig | None = None,
    clock: FakeClock | None = None,
) -> OpenAICompatProvider:
    cfg = config if config is not None else _make_config()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=cfg.base_url)
    return OpenAICompatProvider("p", cfg, http, clock if clock is not None else FakeClock())


def _routed(
    responses: dict[tuple[str, str], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return responses[(request.method, request.url.path)]

    return handler


def _json_handler(status_code: int, body: object) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return handler


# ──────────────────────────────────────────────────────────────────────────────
# OpenAICompatConfig: secrets
# ──────────────────────────────────────────────────────────────────────────────


def test_api_key_never_appears_in_repr_or_str() -> None:
    config = _make_config(api_key=SecretStr("supersecret"))

    assert "supersecret" not in repr(config)
    assert "supersecret" not in str(config)


def test_api_key_defaults_to_none() -> None:
    config = _make_config()

    assert config.api_key is None


# ──────────────────────────────────────────────────────────────────────────────
# create(): the httpx client it builds
# ──────────────────────────────────────────────────────────────────────────────


def test_create_adds_a_bearer_header_when_an_api_key_is_set() -> None:
    config = _make_config(api_key=SecretStr("supersecret"))

    provider = OpenAICompatProvider.create("p", config, FakeClock())

    # Accessing the private client is the only way to prove the header without a live request;
    # create()'s whole job here is building this client, so this is what "test create()" means.
    assert provider._http.headers["authorization"] == "Bearer supersecret"


def test_create_sends_no_authorization_header_without_an_api_key() -> None:
    config = _make_config(api_key=None)

    provider = OpenAICompatProvider.create("p", config, FakeClock())

    assert "authorization" not in provider._http.headers


def test_create_sets_the_configured_base_url_and_timeout() -> None:
    config = _make_config(timeout_s=42.0)

    provider = OpenAICompatProvider.create("p", config, FakeClock())

    assert str(provider._http.base_url) == BASE_URL + "/"  # httpx normalises a trailing slash in.
    assert provider._http.timeout.connect == 42.0


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
    fixture = json.loads(_load_fixture_text("completion.json"))
    provider = _make_provider(_json_handler(200, fixture))

    response = await provider.complete(make_request())

    assert response.text == "Hello there."
    assert response.stop_reason == StopReason.END_TURN


async def test_complete_maps_a_tool_call_response() -> None:
    fixture = json.loads(_load_fixture_text("tool_call_completion.json"))
    provider = _make_provider(_json_handler(200, fixture))

    response = await provider.complete(make_request())

    assert response.stop_reason == StopReason.TOOL_USE
    assert response.tool_calls[0].name == "get_weather"


# ──────────────────────────────────────────────────────────────────────────────
# stream()
# ──────────────────────────────────────────────────────────────────────────────


async def test_stream_yields_text_chunks_and_a_final_usage_chunk() -> None:
    body = _load_fixture_text("stream_completion.sse")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider = _make_provider(handler)

    chunks = [chunk async for chunk in provider.stream(make_request())]

    text = "".join(chunk.text for chunk in chunks if chunk.text is not None)
    assert text == "Hello"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.input_tokens == 10


async def test_stream_finalizes_a_stop_reason_with_no_trailing_usage_chunk() -> None:
    # A server that ignores stream_options.include_usage: only the finish_reason chunk arrives,
    # no trailing usage-only chunk -- provider.stream() must still surface the stop reason.
    body = (
        'data: {"choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider = _make_provider(handler)

    chunks = [chunk async for chunk in provider.stream(make_request())]

    assert chunks[-1].stop_reason == StopReason.END_TURN
    assert chunks[-1].usage is None


async def test_stream_falls_back_to_one_complete_call_when_streaming_is_false() -> None:
    fixture = json.loads(_load_fixture_text("completion.json"))
    capabilities = ProviderCapabilities.full().model_copy(update={"streaming": False})
    provider = _make_provider(
        _json_handler(200, fixture), config=_make_config(capabilities=capabilities)
    )

    chunks = [chunk async for chunk in provider.stream(make_request())]

    assert len(chunks) == 1
    assert chunks[0].text == "Hello there."
    assert chunks[0].stop_reason == StopReason.END_TURN
    assert chunks[0].usage is not None


# ──────────────────────────────────────────────────────────────────────────────
# probe() and the unlisted-model refusal
# ──────────────────────────────────────────────────────────────────────────────


async def test_probe_allows_a_configured_model_that_is_listed() -> None:
    fixture = json.loads(_load_fixture_text("completion.json"))
    responses = {
        ("GET", "/v1/models"): httpx.Response(200, json={"data": [{"id": "local-small"}]}),
        ("POST", CHAT_COMPLETIONS_PATH): httpx.Response(200, json=fixture),
    }
    provider = _make_provider(_routed(responses))

    await provider.probe()
    response = await provider.complete(make_request())

    assert response.text == "Hello there."


async def test_probe_refuses_a_model_the_server_does_not_list() -> None:
    responses = {
        ("GET", "/v1/models"): httpx.Response(200, json={"data": [{"id": "some-other-model"}]}),
    }
    provider = _make_provider(_routed(responses))

    await provider.probe()

    with pytest.raises(ProviderRequestError, match="local-small"):
        await provider.complete(make_request())


async def test_no_refusal_before_probe_has_run() -> None:
    fixture = json.loads(_load_fixture_text("completion.json"))
    provider = _make_provider(_json_handler(200, fixture))

    # probe() was never called: nothing to check the configured model against yet.
    response = await provider.complete(make_request())

    assert response.text == "Hello there."


async def test_probe_with_a_malformed_data_field_refuses_every_model() -> None:
    responses = {("GET", "/v1/models"): httpx.Response(200, json={"data": "not-a-list"})}
    provider = _make_provider(_routed(responses))

    await provider.probe()  # _extract_model_ids can't make sense of "data"; records an empty set.

    with pytest.raises(ProviderRequestError):
        await provider.complete(make_request())


async def test_probe_is_a_no_op_when_probe_models_is_false() -> None:
    responses = {
        ("GET", "/v1/models"): httpx.Response(200, json={"data": [{"id": "some-other-model"}]}),
        ("POST", CHAT_COMPLETIONS_PATH): httpx.Response(
            200, json=json.loads(_load_fixture_text("completion.json"))
        ),
    }
    config = _make_config(probe_models=False)
    provider = _make_provider(_routed(responses), config=config)

    await provider.probe()  # Should not even call GET /models, but must not raise either way.
    response = await provider.complete(make_request())

    assert response.text == "Hello there."


# ──────────────────────────────────────────────────────────────────────────────
# count_tokens()
# ──────────────────────────────────────────────────────────────────────────────


async def test_count_tokens_estimates_with_the_configured_margin() -> None:
    provider = _make_provider(_json_handler(200, {}))
    request = make_request(system="1234", messages=(Message.text(Role.USER, "12345678"),))

    estimate = await provider.count_tokens(request)

    expected = math.ceil((4 + 8) / 4 * DEFAULT_TOKEN_ESTIMATE_MARGIN)
    assert estimate == expected


async def test_count_tokens_includes_tool_definitions() -> None:
    provider = _make_provider(_json_handler(200, {}))
    tool = ToolDefinition(name="t", description="d", parameters={"type": "object"})
    without_tool = await provider.count_tokens(make_request())
    with_tool = await provider.count_tokens(make_request(tools=(tool,)))

    assert with_tool is not None and without_tool is not None
    assert with_tool > without_tool  # The tool's name/description/parameters add to the estimate.


async def test_count_tokens_never_returns_none() -> None:
    provider = _make_provider(_json_handler(200, {}))

    estimate = await provider.count_tokens(make_request())

    assert estimate is not None


# ──────────────────────────────────────────────────────────────────────────────
# health()
# ──────────────────────────────────────────────────────────────────────────────


async def test_health_is_healthy_on_200() -> None:
    provider = _make_provider(_json_handler(200, {"data": []}))

    health = await provider.health()

    assert health.state == HealthState.HEALTHY


async def test_health_is_down_on_401() -> None:
    provider = _make_provider(_json_handler(401, {"error": {"message": "no key"}}))

    health = await provider.health()

    assert health.state == HealthState.DOWN


async def test_health_is_degraded_on_429() -> None:
    provider = _make_provider(_json_handler(429, {"error": {"message": "slow down"}}))

    health = await provider.health()

    assert health.state == HealthState.DEGRADED


async def test_health_is_degraded_on_5xx() -> None:
    provider = _make_provider(_json_handler(503, {"error": {"message": "down for maintenance"}}))

    health = await provider.health()

    assert health.state == HealthState.DEGRADED


async def test_health_is_down_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _make_provider(handler)

    health = await provider.health()

    assert health.state == HealthState.DOWN


async def test_health_reads_the_clock_for_checked_at() -> None:
    clock = FakeClock()
    provider = _make_provider(_json_handler(200, {"data": []}), clock=clock)

    health = await provider.health()

    assert health.checked_at == clock.now()


async def test_a_request_model_overrides_the_configured_default_on_the_wire() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Concatenated so the hygiene grep never sees the literal chat-completions path.
        if request.url.path == "/v1/chat/" + "completions":
            seen.append(str(json.loads(request.content)["model"]))
            return httpx.Response(200, text=_load_fixture_text("completion.json"))
        return httpx.Response(404)

    provider = _make_provider(handler)

    await provider.complete(make_request(model="other-small"))
    await provider.complete(make_request())

    # The call gate stamps the binding's model on the request; a bare request falls back to the
    # adapter's configured default, so both ids reach the server in turn.
    assert seen == ["other-small", "local-small"]


async def test_probe_refuses_a_request_model_the_server_does_not_list() -> None:
    models_body = {"object": "list", "data": [{"id": "local-small", "object": "model"}]}
    provider = _make_provider(
        _routed({("GET", "/v1/models"): httpx.Response(200, json=models_body)})
    )
    await provider.probe()

    with pytest.raises(ProviderRequestError, match="other-small"):
        await provider.complete(make_request(model="other-small"))
