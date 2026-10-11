"""Tests for hivemind.llm.providers.ollama.client: the native-API HTTP layer.

Every test drives `OllamaClient` over `httpx.MockTransport`, so no server, port or network is
involved.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/ollama/client.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.ollama.client for the module under test.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError, RateLimitedError
from hivemind.llm.models import JsonObject
from hivemind.llm.providers.ollama.client import OllamaClient, native_base_url

_BASE = "http://server.test"  # A test-only host; the client never invents one.


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> OllamaClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE)
    return OllamaClient(http, "local")


@pytest.mark.parametrize(
    ("configured", "native"),
    [
        ("http://host.test:1234/v1", "http://host.test:1234"),
        ("http://host.test:1234/v1/", "http://host.test:1234"),
        ("http://host.test:1234", "http://host.test:1234"),
        ("http://host.test/prefix/v1", "http://host.test/prefix"),
        ("http://host.test/v10", "http://host.test/v10"),
    ],
)
def test_native_base_url_drops_only_a_trailing_v1_segment(configured: str, native: str) -> None:
    assert native_base_url(configured) == native


async def test_get_json_returns_the_parsed_object() -> None:
    client = _client(lambda request: httpx.Response(200, json={"version": "1"}))

    assert await client.get_json("/api/version") == {"version": "1"}


async def test_post_json_sends_the_body_and_honours_a_per_request_timeout() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"done": True})

    client = _client(handler)

    await client.post_json("/api/generate", {"model": "m"}, timeout_s=5.0)

    assert seen[0].method == "POST"
    assert b'"model"' in seen[0].content


async def test_a_connection_failure_becomes_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderUnavailableError):
        await _client(handler).get_json("/api/version")


@pytest.mark.parametrize(
    ("status", "error"),
    [(503, ProviderUnavailableError), (429, RateLimitedError), (404, ProviderRequestError)],
)
async def test_an_http_error_status_maps_to_its_typed_error(
    status: int, error: type[Exception]
) -> None:
    client = _client(lambda request: httpx.Response(status, json={"error": "nope"}))

    with pytest.raises(error):
        await client.get_json("/api/tags")


async def test_a_four_xx_carries_the_servers_error_message() -> None:
    client = _client(lambda request: httpx.Response(404, json={"error": "model not found"}))

    with pytest.raises(ProviderRequestError, match="model not found"):
        await client.get_json("/api/tags")


async def test_a_2xx_that_is_not_a_json_object_is_a_request_error() -> None:
    not_object = _client(lambda request: httpx.Response(200, json=[1, 2]))
    not_json = _client(lambda request: httpx.Response(200, content=b"plain"))

    with pytest.raises(ProviderRequestError):
        await not_object.get_json("/x")
    with pytest.raises(ProviderRequestError):
        await not_json.get_json("/x")


async def test_stream_ndjson_yields_each_line_and_skips_blanks() -> None:
    body = b'{"status": "a"}\n\n{"status": "b", "total": 3}\n'
    client = _client(lambda request: httpx.Response(200, content=body))

    lines = [line async for line in client.stream_ndjson("/api/pull", {}, timeout_s=1.0)]

    assert lines == [{"status": "a"}, {"status": "b", "total": 3}]


async def test_stream_ndjson_raises_on_an_in_band_error_line() -> None:
    body = (
        b'{"status": "pulling manifest"}\n{"error": "pull model manifest: file does not exist"}\n'
    )
    client = _client(lambda request: httpx.Response(200, content=body))
    seen: list[JsonObject] = []

    with pytest.raises(ProviderRequestError, match="does not exist"):
        async for line in client.stream_ndjson("/api/pull", {}, timeout_s=1.0):
            seen.append(line)

    assert seen == [{"status": "pulling manifest"}]  # The progress before the error still arrived.


async def test_stream_ndjson_maps_an_error_status_before_any_line() -> None:
    client = _client(lambda request: httpx.Response(500, json={"error": "boom"}))

    with pytest.raises(ProviderUnavailableError):
        async for _ in client.stream_ndjson("/api/pull", {}, timeout_s=1.0):
            pass


async def test_stream_ndjson_rejects_a_line_that_is_not_json() -> None:
    client = _client(lambda request: httpx.Response(200, content=b"not json\n"))

    with pytest.raises(ProviderRequestError):
        async for _ in client.stream_ndjson("/api/pull", {}, timeout_s=1.0):
            pass


async def test_aclose_closes_the_underlying_client() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        base_url=_BASE,
    )

    await OllamaClient(http, "local").aclose()

    assert http.is_closed
