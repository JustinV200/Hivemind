"""Provide TranscriptionClient: the multipart HTTP layer for the /audio/transcriptions wire.

The chat side's `hivemind.llm.providers.openai_compat.client` speaks JSON bodies; a transcription
is a multipart upload of a WAV file, so it gets this small client of its own, built over the same
`httpx.AsyncClient` shape and mapping every failure exactly the way the chat client does: a
transport failure or a 5xx is `ProviderUnavailableError` (try later, or the fallback), a 429 is
`RateLimitedError` carrying the server's `Retry-After`, and every other 4xx is
`ProviderRequestError` (the request itself was refused, so retrying it unchanged cannot help). A
2xx whose body is not a JSON object is refused too, since nothing above this module may ever see
an httpx type or a half-parsed body (codingrules section 8.6).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside
    `hivemind.llm.providers.openai_compat.transcription`. Called by this sub-package's
    `provider` for every transcription; `health()` talks to httpx directly for the raw status.
    Calls into `httpx`, `hivemind.llm.errors` and `hivemind.llm.models` only.

Key invariants:
    - Every request inherits the timeout of the `httpx.AsyncClient` it wraps (set once from the
      provider's `timeout_s` at construction), so no await here is unbounded (codingrules 11).
    - The uploaded audio and the reply text never appear in a raised error's message: only the
      status, the server's own error type and its error message do.

See Also:
    - hivemind.llm.providers.openai_compat.client for the chat client whose mapping this mirrors.
    - hivemind.llm.errors for the typed error tree every failure maps into.
"""

from __future__ import annotations

import httpx
from pydantic import JsonValue

from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError, RateLimitedError
from hivemind.llm.models import JsonObject
from hivemind.llm.providers.openai_compat.transcription.mapping import FilePart

MAX_ERROR_DETAIL_CHARS = 200  # Enough of a server's error message to diagnose, never its body.
_SERVER_ERROR_FLOOR = 500  # 5xx: the server failed, not the request.
_CLIENT_ERROR_FLOOR = 400  # 4xx: the request was refused on its own terms.
_RATE_LIMITED = 429  # The one 4xx that means "wait", not "never".

__all__ = ["MAX_ERROR_DETAIL_CHARS", "TranscriptionClient"]


class TranscriptionClient:
    """A thin multipart layer over one `httpx.AsyncClient`, mapping every failure mode."""

    def __init__(self, http: httpx.AsyncClient, provider: str) -> None:
        """Wrap an already-configured client.

        Args:
            http: Carries the base URL, the bearer header (when a key is set) and the timeout.
                Injected so a test can pass one built on `httpx.MockTransport`.
            provider: The manifest provider name, folded into every raised error.
        """
        self._http = http
        self._provider = provider

    async def post_multipart(
        self, path: str, form: dict[str, str], files: dict[str, FilePart]
    ) -> JsonObject:
        """POST a multipart form to `path` and return the parsed JSON object reply.

        Args:
            path: A path relative to the client's base URL (e.g. "/audio/transcriptions").
            form: The text fields.
            files: The file parts, as (filename, content, media type).

        Returns:
            The parsed JSON object.

        Raises:
            ProviderUnavailableError: A transport failure (connection, timeout, a server that
                hung up mid-reply) or a 5xx.
            RateLimitedError: A 429.
            ProviderRequestError: Any other 4xx, or a 2xx body that is not a JSON object.
        """
        try:
            # External await: an upload of up to 25 MiB plus the server's transcription; bounded
            # by the client's own timeout, and a timeout is a transport failure mapped below.
            response = await self._http.post(path, data=form, files=files)
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(self._provider, f"{type(exc).__name__}: {exc}") from exc
        self._raise_for_status(response)
        return self._parse_object(response)

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise the typed error matching `response`'s status; return quietly for a 2xx or 3xx."""
        status = response.status_code
        if status < _CLIENT_ERROR_FLOOR:
            return
        if status >= _SERVER_ERROR_FLOOR:
            raise ProviderUnavailableError(self._provider, f"HTTP {status}")
        if status == _RATE_LIMITED:
            raise RateLimitedError(self._provider, retry_after_s=_retry_after_s(response))
        message, error_type = _read_error(response)
        raise ProviderRequestError(self._provider, status, error_type, detail=message)

    def _parse_object(self, response: httpx.Response) -> JsonObject:
        """Parse a 2xx body as one JSON object, refusing anything else."""
        try:
            parsed: JsonValue = response.json()
        except ValueError as exc:
            raise self._not_an_object(response.status_code) from exc
        if not isinstance(parsed, dict):
            raise self._not_an_object(response.status_code)
        return parsed

    def _not_an_object(self, status: int) -> ProviderRequestError:
        """Build the error for a 2xx whose body is not one JSON object."""
        return ProviderRequestError(
            self._provider, status, detail="the reply body was not a JSON object"
        )


def _read_error(response: httpx.Response) -> tuple[str, str | None]:
    """Extract a short message and an OpenAI-style error `type` from an error body."""
    try:
        payload: JsonValue = response.json()
    except ValueError:
        return _clip(response.text), None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return _clip(response.text), None
    message, error_type = error.get("message"), error.get("type")
    return (
        _clip(message) if isinstance(message, str) else _clip(response.text),
        error_type if isinstance(error_type, str) else None,
    )


def _retry_after_s(response: httpx.Response) -> float | None:
    """Parse a 429's `Retry-After` as seconds; None when absent or in HTTP-date form."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # An HTTP-date Retry-After: rare, and the Fanner has a default wait.


def _clip(text: str) -> str:
    """Shorten an error message to `MAX_ERROR_DETAIL_CHARS`, on one line."""
    flat = text.replace("\n", " ").strip()
    return flat if len(flat) <= MAX_ERROR_DETAIL_CHARS else flat[:MAX_ERROR_DETAIL_CHARS] + "..."
