"""Tests for hivemind.llm.providers.ollama.manage: OllamaManager over a mocked native API.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/ollama/manage.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.ollama.manage for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from hivemind.llm.errors import MalformedOutputError, ProviderRequestError, ProviderUnavailableError
from hivemind.llm.providers.ollama.manage import OllamaManager

_BASE_URL = "http://server.test:4321/v1"  # The OpenAI-compatible URL a manifest would carry.
_PROVIDER = "local"

Handler = Callable[[httpx.Request], httpx.Response]


def _manager(handler: Handler) -> OllamaManager:
    return OllamaManager.create(_PROVIDER, _BASE_URL, 5.0, transport=httpx.MockTransport(handler))


def _ollama(request: httpx.Request) -> httpx.Response:
    """Answer like an Ollama server for the endpoints these tests use."""
    routes: dict[str, httpx.Response] = {
        "/api/version": httpx.Response(200, json={"version": "9.9.9"}),
        "/api/tags": httpx.Response(
            200, json={"models": [{"name": "local-small", "size": 10}, {"name": "local-big"}]}
        ),
        "/api/ps": httpx.Response(200, json={"models": [{"name": "local-small"}]}),
        "/api/generate": httpx.Response(200, json={"done": True}),
    }
    return routes.get(request.url.path, httpx.Response(404, json={"error": "no route"}))


async def test_detect_is_true_for_a_server_answering_the_native_version_probe() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host}:{request.url.port}{request.url.path}")
        return _ollama(request)

    detected = await OllamaManager.detect(_BASE_URL, transport=httpx.MockTransport(handler))

    assert detected is True
    # The native API is on the same host and port as the configured URL, without its /v1.
    assert seen == ["server.test:4321/api/version"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"error": "nope"}),
        httpx.Response(200, json={"not_version": 1}),
        httpx.Response(200, content=b"<html>"),
        httpx.Response(500),
    ],
)
async def test_detect_is_false_for_a_server_that_is_not_ollama(response: httpx.Response) -> None:
    detected = await OllamaManager.detect(
        _BASE_URL, transport=httpx.MockTransport(lambda request: response)
    )

    assert detected is False


async def test_detect_is_false_for_an_unreachable_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await OllamaManager.detect(_BASE_URL, transport=httpx.MockTransport(handler)) is False


async def test_detect_is_false_for_a_base_url_without_a_scheme() -> None:
    # No transport is injected: httpx refuses the URL before any connection is attempted.
    assert await OllamaManager.detect("localhost:11434/v1", timeout_s=0.5) is False


async def test_open_closes_its_client_when_the_probe_fails_unexpectedly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    def _boom(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("not an httpx failure at all")

    class _Tracked(httpx.AsyncClient):  # Bound here, before the patch below replaces the name.
        """An AsyncClient that records its close."""

        async def aclose(self) -> None:
            closed.append(True)
            await super().aclose()

    def build(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_boom)
        return _Tracked(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", build)

    with pytest.raises(RuntimeError):
        await OllamaManager.open(_PROVIDER, _BASE_URL, 5.0)

    assert closed == [True]


async def test_open_returns_none_and_closes_for_a_base_url_without_a_scheme() -> None:
    assert await OllamaManager.open(_PROVIDER, "localhost:11434/v1", 0.5) is None


async def test_list_models_returns_the_installed_models() -> None:
    manager = _manager(_ollama)

    models = await manager.list_models()

    assert [model.name for model in models] == ["local-small", "local-big"]
    assert models[0].size == 10


async def test_list_models_raises_malformed_output_for_a_wrong_shape() -> None:
    manager = _manager(lambda request: httpx.Response(200, json={"models": [{"size": 1}]}))

    with pytest.raises(MalformedOutputError):
        await manager.list_models()


async def test_pull_streams_progress_lines_in_order() -> None:
    body = (
        b'{"status": "pulling manifest"}\n'
        b'{"status": "pulling abc", "digest": "abc", "total": 100, "completed": 40}\n'
        b'{"status": "success"}\n'
    )
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=body)

    manager = _manager(handler)

    lines = [line async for line in manager.pull("local-small")]

    assert [line.status for line in lines] == ["pulling manifest", "pulling abc", "success"]
    assert lines[1].fraction == pytest.approx(0.4)
    assert lines[1].digest == "abc"
    assert seen == [{"model": "local-small", "stream": True}]


async def test_pull_skips_lines_without_a_status_and_bad_counts() -> None:
    body = b'{"nothing": 1}\n{"status": "x", "total": -4, "completed": true}\n'
    manager = _manager(lambda request: httpx.Response(200, content=body))

    lines = [line async for line in manager.pull("m")]

    assert len(lines) == 1
    assert lines[0].total is None
    assert lines[0].completed is None


async def test_pull_of_an_unknown_model_raises_a_request_error() -> None:
    body = b'{"error": "pull model manifest: file does not exist"}\n'
    manager = _manager(lambda request: httpx.Response(200, content=body))

    with pytest.raises(ProviderRequestError, match="does not exist"):
        async for _ in manager.pull("missing"):
            pass


async def test_keep_alive_sends_the_duration_with_no_prompt() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ollama(request)

    manager = _manager(handler)

    await manager.keep_alive("local-small", 300)

    assert seen == [{"model": "local-small", "keep_alive": 300, "stream": False}]


async def test_keep_alive_rejects_a_duration_below_minus_one() -> None:
    manager = _manager(_ollama)

    with pytest.raises(ValueError, match="duration_s"):
        await manager.keep_alive("m", -2)


async def test_keep_alive_for_a_model_the_server_lacks_raises() -> None:
    manager = _manager(lambda request: httpx.Response(404, json={"error": "model not found"}))

    with pytest.raises(ProviderRequestError, match="not found"):
        await manager.keep_alive("missing", 60)


async def test_health_reports_the_version_and_the_loaded_models() -> None:
    manager = _manager(_ollama)

    health = await manager.health()

    assert health.version == "9.9.9"
    assert [model.name for model in health.loaded] == ["local-small"]


async def test_health_of_a_down_server_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderUnavailableError):
        await _manager(handler).health()


async def test_open_returns_a_manager_for_ollama_and_none_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `open` builds its own client, so route every client it makes through a mock transport.
    real_client = httpx.AsyncClient

    def with_transport(handler: Handler) -> Callable[..., httpx.AsyncClient]:
        def build(**kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(**kwargs)  # type: ignore[arg-type]

        return build

    monkeypatch.setattr(httpx, "AsyncClient", with_transport(_ollama))
    found = await OllamaManager.open(_PROVIDER, _BASE_URL, 5.0)
    monkeypatch.setattr(httpx, "AsyncClient", with_transport(lambda request: httpx.Response(404)))
    missing = await OllamaManager.open(_PROVIDER, _BASE_URL, 5.0)

    assert found is not None
    assert missing is None
    if found is not None:
        await found.aclose()
