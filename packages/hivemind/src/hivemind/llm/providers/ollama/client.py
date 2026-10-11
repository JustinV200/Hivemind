"""Provide OllamaClient: one small async HTTP layer over httpx for Ollama's native API.

The native API lives beside the OpenAI-compatible one on the same server, so its base URL is
derived from the provider's configured `base_url` by dropping a trailing `/v1` (the manifest
convention for the OpenAI-compatible door); the host and port are whatever the operator wrote,
never a default (codingrules 8.6: provider URLs come from the manifest). This is the only module
in the Ollama package that touches `httpx`, and the only place an `httpx` failure is mapped: a
connection failure or 5xx becomes `ProviderUnavailableError`, a 429 `RateLimitedError`, any other
4xx `ProviderRequestError`, so nothing above this module catches an `httpx.*` exception.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers.ollama`. Called by
    `manage.OllamaManager` for every request. Calls into `httpx` and `hivemind.llm.errors` only.

Key invariants:
    - The base URL is never given a default port or host: `native_base_url` only edits what the
      operator wrote.
    - A streamed reply is newline-delimited JSON; a line carrying an `error` key raises
      `ProviderRequestError` rather than being yielded, because Ollama reports a failed pull
      in-band after answering 200.
    - Every request inherits a timeout: the client's, or an explicit longer one for a pull or a
      model load that is expected to take minutes.

See Also:
    - hivemind.llm.providers.openai_compat.client for the sibling client of the other door.
    - hivemind.llm.errors for the typed errors raised here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from pydantic import JsonValue

from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError, RateLimitedError
from hivemind.llm.models import JsonObject

OPENAI_PATH_SUFFIX = "/v1"  # The segment the OpenAI-compatible door adds to the server's root.
# Connection-level httpx exceptions that mean "the server never answered" (or hung up mid-call).
_CONNECTION_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.CloseError,
    httpx.RemoteProtocolError,
)

__all__ = ["OPENAI_PATH_SUFFIX", "OllamaClient", "native_base_url"]


def native_base_url(base_url: str) -> str:
    """Derive the native API's base URL from a provider's configured `base_url`.

    Args:
        base_url: The provider's `[llm.providers.<name>] base_url`, usually ending in `/v1`.

    Returns:
        The same URL without a trailing slash and without a trailing `/v1` segment.
    """
    trimmed = base_url.rstrip("/")
    if trimmed.endswith(OPENAI_PATH_SUFFIX):
        trimmed = trimmed[: -len(OPENAI_PATH_SUFFIX)]
    return trimmed.rstrip("/")


class OllamaClient:
    """A thin async JSON and NDJSON layer over one `httpx.AsyncClient`, mapping every failure."""

    def __init__(self, http: httpx.AsyncClient, provider: str) -> None:
        """Wrap an already-configured client.

        Args:
            http: The underlying client; its `base_url` is the native base (`native_base_url`)
                and its timeout the default for requests that do not pass their own. Injected so
                a test can build it on `httpx.MockTransport`.
            provider: The manifest provider name, folded into every raised error.
        """
        self._http = http
        self._provider = provider

    async def get_json(self, path: str) -> JsonObject:
        """GET `path` and return its parsed JSON object.

        Raises:
            ProviderUnavailableError: A connection failure, timeout or 5xx.
            RateLimitedError: A 429.
            ProviderRequestError: Any other 4xx, or a 2xx body that is not a JSON object.
        """
        return await self._send("GET", path, None, None)

    async def post_json(
        self, path: str, body: JsonObject, *, timeout_s: float | None = None
    ) -> JsonObject:
        """POST `body` as JSON to `path` and return the parsed JSON object.

        Args:
            path: A path relative to the native base URL.
            body: The JSON request body.
            timeout_s: A per-request timeout overriding the client's, for a model load that
                legitimately takes minutes; None keeps the client's.

        Raises:
            ProviderUnavailableError: A connection failure, timeout or 5xx.
            RateLimitedError: A 429.
            ProviderRequestError: Any other 4xx, or a 2xx body that is not a JSON object.
        """
        return await self._send("POST", path, body, timeout_s)

    async def stream_ndjson(
        self, path: str, body: JsonObject, *, timeout_s: float
    ) -> AsyncIterator[JsonObject]:
        """POST `body` to `path` and yield each newline-delimited JSON object the reply streams.

        Args:
            path: A path relative to the native base URL.
            body: The JSON request body; the caller has set `"stream": true`.
            timeout_s: Seconds the stream may stay silent before the read times out; a pull of a
                large layer can pause between progress lines, so this is longer than a probe's.

        Yields:
            One object per non-blank line, in order.

        Raises:
            ProviderUnavailableError: A connection failure, timeout or 5xx.
            RateLimitedError: A 429.
            ProviderRequestError: Any other 4xx, or an in-band `{"error": ...}` line.
        """
        try:
            # External await: a long download; `timeout_s` bounds each silent gap, not the total.
            async with self._http.stream("POST", path, json=body, timeout=timeout_s) as response:
                if response.status_code >= 400:
                    await response.aread()  # An error body must be read before it can be parsed.
                    self._raise_for_status(response)
                async for line in response.aiter_lines():
                    parsed = self._parse_line(line)
                    if parsed is not None:
                        yield parsed
        except _CONNECTION_EXCEPTIONS as exc:
            raise ProviderUnavailableError(self._provider, _describe(exc)) from exc

    async def aclose(self) -> None:
        """Close the underlying client and its pooled connections."""
        await self._http.aclose()

    async def _send(
        self, method: str, path: str, body: JsonObject | None, timeout_s: float | None
    ) -> JsonObject:
        """Send one non-streaming request and return its parsed JSON object body."""
        try:
            # External await: a probe or a model load; the client's timeout (or the explicit one
            # for a load) turns a hung server into ProviderUnavailableError below.
            if timeout_s is None:
                response = await self._http.request(method, path, json=body)
            else:
                response = await self._http.request(method, path, json=body, timeout=timeout_s)
        except _CONNECTION_EXCEPTIONS as exc:
            raise ProviderUnavailableError(self._provider, _describe(exc)) from exc
        self._raise_for_status(response)
        return self._json_object(response)

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise the typed error matching `response`'s status, or return for a 2xx."""
        if response.status_code < 400:
            return
        if response.status_code >= 500:
            raise ProviderUnavailableError(self._provider, f"HTTP {response.status_code}")
        if response.status_code == 429:
            raise RateLimitedError(self._provider)
        raise ProviderRequestError(
            self._provider, response.status_code, detail=_error_message(response)
        )

    def _json_object(self, response: httpx.Response) -> JsonObject:
        """Parse a 2xx body as a JSON object, or raise ProviderRequestError."""
        try:
            parsed: JsonValue = response.json()
        except ValueError as exc:
            raise ProviderRequestError(
                self._provider, response.status_code, detail="response body was not JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderRequestError(
                self._provider, response.status_code, detail="response body was not an object"
            )
        return parsed

    def _parse_line(self, line: str) -> JsonObject | None:
        """Parse one NDJSON line; None for a blank one, ProviderRequestError for an error line."""
        stripped = line.strip()
        if not stripped:
            return None
        try:
            parsed: JsonValue = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ProviderRequestError(
                self._provider, 200, detail="a streamed line was not JSON"
            ) from exc
        if not isinstance(parsed, dict):
            return None
        error = parsed.get("error")
        if isinstance(error, str):
            # Ollama reports a failed pull in-band, after the 200 that opened the stream.
            raise ProviderRequestError(self._provider, 200, detail=error)
        return parsed


def _error_message(response: httpx.Response) -> str:
    """Extract Ollama's `{"error": "..."}` message from an error body, or the raw text."""
    try:
        payload: JsonValue = response.json()
    except ValueError:
        return response.text
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, str) else response.text


def _describe(exc: Exception) -> str:
    """Render a short, secret-free description of a connection-level exception."""
    return f"{type(exc).__name__}: {exc}"
