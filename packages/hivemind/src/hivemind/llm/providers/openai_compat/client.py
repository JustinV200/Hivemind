"""Provide OpenAICompatClient: one small async HTTP layer over httpx for the OpenAI-compatible wire.

This is the only place `httpx` request/response mechanics (status codes, headers, SSE framing)
are handled for this adapter; `hivemind.llm.providers.openai_compat.provider` calls
`get_json`/`post_json`/`stream_sse` and never touches `httpx.Response` itself. Every httpx-specific
error (a connection failure, a timeout, an HTTP status) is mapped here to one of
`hivemind.llm.errors`'s typed `LLMError`s, so nothing above this module ever catches an `httpx.*`
exception (codingrules section 8.6: adapters raise typed errors, never a vendor exception).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.
    openai_compat`. Called by `OpenAICompatProvider` (`provider.py`) for every network operation
    except `health()`, which talks to `httpx.AsyncClient` directly because its DEGRADED/DOWN
    distinction needs the raw status code, not this module's coarser error mapping. Calls into
    `httpx` and `hivemind.llm.errors` only.

Key invariants:
    - Every httpx call this module makes inherits its timeout from the `httpx.AsyncClient` it was
      constructed with (`OpenAICompatConfig.timeout_s`, applied once at client construction in
      `provider.py`); codingrules section 11 ("every external await has a timeout") is satisfied
      there, not per-call here.
    - A 5xx or a connection failure both become `ProviderUnavailableError`: from a caller's
      perspective both mean "try again later, possibly on a fallback", so they share a type.
    - `stream_sse`'s error path fully reads the response body (`aread()`) before mapping it, since
      an unread streaming response has no `.json()`/`.text` available yet.

See Also:
    - .claude/codingrules.md section 8.6 for "vendor SDKs and model-server HTTP clients are
      imported only inside llm/providers/<name>/".
    - .claude/codingrules.md section 11 for the external-await-timeout rule.
    - hivemind.llm.errors for the typed error tree this module maps every httpx failure into.
    - hivemind.llm.providers.openai_compat.provider for OpenAICompatProvider, this module's caller.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from pydantic import JsonValue

from hivemind.llm.errors import (
    ContextTooLongError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from hivemind.llm.models import JsonObject

SSE_DONE_SENTINEL = "[DONE]"  # The OpenAI-compatible SSE stream's own end-of-stream marker line.
SSE_DATA_PREFIX = "data:"  # Every payload-carrying SSE line starts with this; others are ignored.
# A 400's error message containing any of these (case-insensitively) is the request overflowing
# the model's context window, not a generic bad request -- these four phrasings cover every
# OpenAI-compatible server this adapter has been checked against (brief section 2's client.py).
CONTEXT_OVERFLOW_MARKERS = (
    "context length",
    "maximum context",
    "too many tokens",
    "context_length_exceeded",
)
# Connection-level httpx exceptions that mean "the server never answered", as distinct from an
# HTTP status the server did send; both end up as ProviderUnavailableError, but only these are
# caught here (an httpx.HTTPStatusError never happens because this client never calls
# response.raise_for_status() itself -- it inspects response.status_code directly instead).
_CONNECTION_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)

__all__ = ["OpenAICompatClient"]


class OpenAICompatClient:
    """A thin async JSON/SSE layer over one `httpx.AsyncClient`, mapping every failure mode.

    Not safe to share a in-flight streaming call across tasks (an `httpx.AsyncClient` itself is
    safe for concurrent requests; this wrapper adds no additional state of its own, so the usual
    httpx concurrency guarantees apply unchanged).
    """

    def __init__(self, http: httpx.AsyncClient, provider: str, context_window: int) -> None:
        """Create a client wrapping an already-configured `httpx.AsyncClient`.

        Args:
            http: The underlying client; its `base_url`, headers and timeout are already set by
                `OpenAICompatProvider.create`. Injected (rather than built here) so tests can pass
                one built on `httpx.MockTransport`.
            provider: The manifest provider name, folded into every raised `LLMError`.
            context_window: The bound model's context window, for `ContextTooLongError`.
        """
        self._http = http
        self._provider = provider
        self._context_window = context_window

    async def get_json(self, path: str) -> JsonObject:
        """GET `path` and return its parsed JSON object body.

        Args:
            path: A path relative to the client's `base_url` (e.g. `"/models"`).

        Returns:
            The parsed JSON object.

        Raises:
            ProviderUnavailableError: A connection failure, timeout, or 5xx response.
            RateLimitedError: A 429 response.
            ContextTooLongError: A 400 whose message describes a context-length overflow.
            ProviderRequestError: Any other 4xx response, or a 2xx body that is not a JSON object.
        """
        return await self._request_json("GET", path, None)

    async def post_json(self, path: str, body: JsonObject) -> JsonObject:
        """POST `body` as JSON to `path` and return the parsed JSON object response.

        Args:
            path: A path relative to the client's `base_url` (e.g. `"/chat/completions"`).
            body: The JSON request body.

        Returns:
            The parsed JSON object.

        Raises:
            ProviderUnavailableError: A connection failure, timeout, or 5xx response.
            RateLimitedError: A 429 response.
            ContextTooLongError: A 400 whose message describes a context-length overflow.
            ProviderRequestError: Any other 4xx response, or a 2xx body that is not a JSON object.
        """
        return await self._request_json("POST", path, body)

    async def stream_sse(self, path: str, body: JsonObject) -> AsyncIterator[JsonObject]:
        """POST `body` to `path` and yield each SSE `data:` line's parsed JSON object.

        Args:
            path: A path relative to the client's `base_url` (e.g. `"/chat/completions"`).
            body: The JSON request body; the caller has already set `"stream": True`.

        Yields:
            One parsed JSON object per `data:` line, in order, until the server's `[DONE]` line
            (never yielded itself) or the connection closes.

        Raises:
            ProviderUnavailableError: A connection failure, timeout, or 5xx response.
            RateLimitedError: A 429 response.
            ContextTooLongError: A 400 whose message describes a context-length overflow.
            ProviderRequestError: Any other 4xx response.
        """
        try:
            # httpx.AsyncClient.stream is a context manager so the connection is released as soon
            # as this generator is closed (early break, exception, or natural exhaustion) instead
            # of only when the whole response would otherwise have been buffered.
            async with self._http.stream("POST", path, json=body) as response:
                if response.status_code >= 400:
                    # An error response to a streaming POST is still a small JSON body, not an
                    # SSE stream -- it must be fully read before it can be inspected at all.
                    await response.aread()
                    self._raise_for_status(response)
                    return
                async for line in response.aiter_lines():
                    is_done, parsed = _parse_sse_line(line)
                    if is_done:
                        return
                    if parsed is not None:
                        yield parsed
        except _CONNECTION_EXCEPTIONS as exc:
            raise ProviderUnavailableError(self._provider, _describe(exc)) from exc

    async def _request_json(self, method: str, path: str, body: JsonObject | None) -> JsonObject:
        """Send one non-streaming request and return its parsed JSON object body."""
        try:
            response = await self._http.request(method, path, json=body)
        except _CONNECTION_EXCEPTIONS as exc:
            raise ProviderUnavailableError(self._provider, _describe(exc)) from exc
        self._raise_for_status(response)
        parsed: JsonValue = response.json()
        if not isinstance(parsed, dict):
            raise ProviderRequestError(
                self._provider, response.status_code, detail="response body was not a JSON object"
            )
        return parsed

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise the typed error matching `response`'s status, or return for a 2xx.

        Args:
            response: An already fully-read response (buffered by default for a non-streaming
                request; explicitly `aread()`-ed by `stream_sse` for its error path).
        """
        if response.status_code < 400:
            return
        if response.status_code >= 500:
            raise ProviderUnavailableError(self._provider, f"HTTP {response.status_code}")
        if response.status_code == 429:
            raise RateLimitedError(self._provider, retry_after_s=_parse_retry_after(response))
        message, error_type = _read_error(response)
        if response.status_code == 400 and _looks_like_context_overflow(message):
            raise ContextTooLongError(self._provider, window=self._context_window)
        raise ProviderRequestError(self._provider, response.status_code, error_type, detail=message)


def _parse_sse_line(line: str) -> tuple[bool, JsonObject | None]:
    """Parse one SSE line into `(is_done, payload)`.

    Args:
        line: One line from `httpx.Response.aiter_lines()`, with or without its own newline.

    Returns:
        `(True, None)` for the stream's own `[DONE]` marker; `(False, None)` for a line that
        carries no JSON payload (blank, or an SSE field this adapter does not use, e.g. `event:`);
        `(False, <parsed object>)` for a `data:` line carrying one.
    """
    stripped = line.strip()
    if not stripped or not stripped.startswith(SSE_DATA_PREFIX):
        return False, None
    data = stripped[len(SSE_DATA_PREFIX) :].strip()
    if data == SSE_DONE_SENTINEL:
        return True, None
    parsed: JsonValue = json.loads(data)
    return False, (parsed if isinstance(parsed, dict) else {})


def _read_error(response: httpx.Response) -> tuple[str, str | None]:
    """Extract a human-readable message and an OpenAI-style error `type` from an error body."""
    try:
        payload: JsonValue = response.json()
    except ValueError:
        return response.text, None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        error_type = error.get("type")
        return (
            message if isinstance(message, str) else response.text,
            error_type if isinstance(error_type, str) else None,
        )
    return response.text, None


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse a 429's `Retry-After` header as seconds, or None when absent or not a plain number."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # An HTTP-date form Retry-After; not worth parsing for this adapter.


def _looks_like_context_overflow(message: str) -> bool:
    """Return whether `message` names a context-length overflow rather than a generic 400."""
    lowered = message.lower()
    return any(marker in lowered for marker in CONTEXT_OVERFLOW_MARKERS)


def _describe(exc: Exception) -> str:
    """Render a short, secret-free description of a connection-level exception."""
    return f"{type(exc).__name__}: {exc}"
