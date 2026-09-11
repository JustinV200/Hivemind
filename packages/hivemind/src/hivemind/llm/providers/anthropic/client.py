"""Provide AnthropicClient: a thin async wrapper over the anthropic SDK's Messages/Models calls.

This is the only place `anthropic.AsyncAnthropic` methods are actually invoked; `hivemind.llm.
providers.anthropic.provider` never touches the SDK client directly. `create`, `stream` and
`count_tokens` each wrap one SDK call and re-raise every SDK exception as a typed
`hivemind.llm.errors.LLMError` through `map_error`, so nothing above this module ever catches an
`anthropic.*` exception (codingrules section 8.6: adapters raise typed errors, never a vendor
exception). `probe_health` is `LLMProvider.health`'s implementation: it never raises, turning every
outcome -- including a raised SDK exception -- into a `ProviderHealth` reading instead.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.anthropic`.
    Called by `AnthropicProvider` (`provider.py`) for every network operation. Calls into the
    `anthropic` SDK and `hivemind.llm.errors`/`hivemind.llm.capabilities` only; never reads a
    vendor wire field by name (that is `mapping.py`'s and `streaming.py`'s job).

Key invariants:
    - `max_retries=0` is set once, by `AnthropicProvider.from_config`, not here: this module never
      retries on its own (codingrules section 11: the Fanner and the ladders own retry policy).
    - `map_error` never lets a vendor exception's `str()` leak the API key: the key never appears
      in an SDK exception's message or body in the first place (it lives only in the request
      header `anthropic.AsyncAnthropic` builds internally), so no redaction step is needed here.
    - `probe_health` never raises: every failure mode, SDK exception included, becomes a
      `ProviderHealth` reading, per `LLMProvider.health`'s contract.

See Also:
    - .claude/codingrules.md section 8.6 for the "core code branches on capabilities, never on
      provider name" rule this module's error mapping extends to error handling.
    - .claude/codingrules.md section 11 for the external-await-timeout and no-own-retries rules.
    - hivemind.llm.errors for the typed error tree this module maps every anthropic failure into.
    - hivemind.llm.providers.anthropic.provider for AnthropicProvider, this module's caller.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import anthropic
import anthropic.types as at

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.errors import (
    ContextTooLongError,
    LLMError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from hivemind.llm.models import JsonObject
from waggle.clock import Clock

# BadRequestError message substrings that mean "the prompt itself overflowed the context window",
# distinct from every other 400 (a malformed tool schema, an unknown field, ...); the two
# phrasings the Anthropic API actually uses, matched case-insensitively.
CONTEXT_OVERFLOW_MARKERS = ("prompt is too long", "too many tokens")
# >=500 is "try again later, possibly on a fallback" regardless of which of the SDK's several
# 5xx-shaped subclasses raised it (InternalServerError's generic >=500, OverloadedError's 529,
# ServiceUnavailableError's 503, DeadlineExceededError's 504): they share no common base beyond
# APIStatusError, so the status code itself is the one future-proof discriminator.
UNAVAILABLE_STATUS_FLOOR = 500
# Synthesizes a status for a mapped error with no real HTTP response behind it: an AnthropicError
# subtype (a future SDK addition) this module has not special-cased.
UNKNOWN_ERROR_STATUS_CODE = 0
# The health detail for a provider the Hive holds no credentials for. The SDK resolves credentials
# lazily, at header-build time, and raises a bare TypeError rather than one of its own APIError
# types when it finds none, so probe_health reads their absence directly instead of discovering it
# by exception (see probe_health's own "Never raises"). The test is falsiness, not `is None`:
# `AnthropicProvider.from_config` deliberately passes an empty string for an unset key so that
# building a provider never raises (provider.py's own "Key invariants").
NO_CREDENTIALS_DETAIL = "no API key configured"

__all__ = ["NO_CREDENTIALS_DETAIL", "AnthropicClient", "map_error"]


class AnthropicClient:
    """Wrap one `anthropic.AsyncAnthropic` instance: create, stream, count_tokens, probe_health.

    Not safe to share an in-flight `stream()` call across tasks; the underlying
    `AsyncAnthropic` itself is safe for concurrent independent calls (its own guarantee), and this
    wrapper adds no shared state beyond the client and the two labels used in every error.
    """

    def __init__(self, sdk: anthropic.AsyncAnthropic, provider: str, context_window: int) -> None:
        """Wrap an already-constructed SDK client.

        Args:
            sdk: The `anthropic.AsyncAnthropic` this client calls; built by
                `AnthropicProvider.from_config` in production, or directly over the SDK's mock
                transport in tests.
            provider: The manifest provider name, folded into every raised `LLMError`.
            context_window: The bound model's context window, for `ContextTooLongError`.
        """
        self._sdk = sdk
        self._provider = provider
        self._context_window = context_window

    async def create(self, params: JsonObject) -> at.Message:
        """Run one non-streaming `messages.create` call.

        Args:
            params: A request body built by `mapping.to_create_params`.

        Returns:
            The SDK's own parsed `Message`.

        Raises:
            RateLimitedError: The API rate-limited this call.
            ProviderUnavailableError: A connection failure, timeout, or 5xx-shaped response.
            ContextTooLongError: A 400 whose message describes a context-length overflow.
            ProviderRequestError: Any other 4xx rejection.
        """
        try:
            # codingrules section 11: this await inherits the SDK client's configured timeout
            # (AnthropicProvider.from_config's timeout_s). params is built dynamically by
            # mapping.py, so it cannot be checked against messages.create's precise per-keyword
            # signature statically; mirrors tests/builders/llm.py's make_bound for the same reason.
            result = await self._sdk.messages.create(**params)  # type: ignore[call-overload]
        except anthropic.APIError as exc:
            raise map_error(exc, self._provider, context_window=self._context_window) from exc
        if isinstance(result, at.Message):
            return result
        # SAFETY: messages.create's overloads pick Message vs. AsyncStream by a literal `stream=`
        # kwarg; params (built dynamically) never sets one, so the SDK always returns a Message
        # in practice. A real, typed raise (not `assert`, which ruff's S101 bans outside tests
        # and which `-O` can strip) narrows the return type for mypy and stays safe either way.
        raise ProviderRequestError(
            self._provider,
            UNKNOWN_ERROR_STATUS_CODE,
            error_type="unexpected_stream_response",
            detail="messages.create() returned a stream from a non-streaming call",
        )

    async def stream(
        self, params: JsonObject
    ) -> AsyncIterator[anthropic.MessageStreamEvent | at.Message]:
        """Run one `messages.stream` call, yielding every parsed event and then the final message.

        `messages.stream()`'s own async iterator yields `anthropic.MessageStreamEvent`: a mix of
        the raw SSE-shaped events (`message_start`, `content_block_start`, ...) and the SDK's own
        synthesized convenience events (`TextEvent` with a plain `.text` string, `InputJsonEvent`,
        ...); this method is a pure passthrough of whatever that iterator yields.

        Args:
            params: A request body built by `mapping.to_create_params`.

        Yields:
            Each `MessageStreamEvent` as it arrives, in order; then, as the last item, the
            stream's own fully accumulated final `Message` (from `get_final_message()`), which
            `hivemind.llm.providers.anthropic.streaming.accumulate` uses for the tool-call and
            usage/stop_reason chunks the event stream alone does not cheaply reconstruct.

        Raises:
            RateLimitedError: The API rate-limited this call.
            ProviderUnavailableError: A connection failure, timeout, or 5xx-shaped response.
            ContextTooLongError: A 400 whose message describes a context-length overflow.
            ProviderRequestError: Any other 4xx rejection.
        """
        try:
            # See create()'s comment on the type: ignore; same dynamic-kwargs call shape.
            async with self._sdk.messages.stream(**params) as stream:  # type: ignore[arg-type]
                async for event in stream:
                    # The SDK's own AsyncMessageStream is generic over an output-format type
                    # parameter this adapter never binds; two of its member types come back
                    # parameterized (e.g. ParsedMessageStopEvent[None]) in a shape mypy cannot
                    # match against the plain `anthropic.MessageStreamEvent` alias declared
                    # below, though both describe the exact same runtime objects.
                    yield cast("anthropic.MessageStreamEvent", event)
                final_message = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise map_error(exc, self._provider, context_window=self._context_window) from exc
        yield final_message

    async def count_tokens(self, params: JsonObject) -> int:
        """Run one `messages.count_tokens` call.

        Args:
            params: A request body built by `mapping.to_count_params`.

        Returns:
            The API's own token estimate for the request.

        Raises:
            RateLimitedError: The API rate-limited this call.
            ProviderUnavailableError: A connection failure, timeout, or 5xx-shaped response.
            ProviderRequestError: Any other 4xx rejection.
        """
        try:
            result = await self._sdk.messages.count_tokens(**params)  # type: ignore[arg-type]
        except anthropic.APIError as exc:
            raise map_error(exc, self._provider, context_window=self._context_window) from exc
        return result.input_tokens

    async def probe_health(self, clock: Clock) -> ProviderHealth:
        """Probe the API with `models.list(limit=1)`; see `LLMProvider.health`.

        No model id is needed for this probe (codingrules 8.6): it only proves the credentials and
        the endpoint are reachable, which is all `health()` is asked to report.

        Args:
            clock: Source of the reading's `checked_at`.

        Returns:
            HEALTHY on a 200; DEGRADED on a 429 or any 5xx-shaped status; DOWN on a connection
            failure or any other rejection (401 included); DOWN with `NO_CREDENTIALS_DETAIL` when
            no credentials are configured at all. Never raises.
        """
        # An unconfigured provider is one this Hive cannot reach, which is a reading rather than a
        # failure: report it without a round trip, and before the SDK can raise TypeError out of a
        # function whose contract above is that it never raises.
        if not self._sdk.api_key and not self._sdk.auth_token:
            return ProviderHealth(
                state=HealthState.DOWN, detail=NO_CREDENTIALS_DETAIL, checked_at=clock.now()
            )

        try:
            # codingrules section 11: this await inherits the SDK client's configured timeout.
            await self._sdk.models.list(limit=1)
        except anthropic.APIStatusError as exc:
            state = HealthState.DEGRADED if _is_degraded_status(exc) else HealthState.DOWN
            return ProviderHealth(state=state, detail=_describe(exc), checked_at=clock.now())
        except anthropic.APIConnectionError as exc:
            return ProviderHealth(
                state=HealthState.DOWN, detail=_describe(exc), checked_at=clock.now()
            )
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=clock.now())


def map_error(exc: anthropic.APIError, provider: str, *, context_window: int) -> LLMError:
    """Map one `anthropic.APIError` to the matching typed `LLMError`.

    Args:
        exc: The SDK exception `create`/`stream`/`count_tokens` caught.
        provider: The manifest provider name, folded into the returned error.
        context_window: The bound model's context window, for `ContextTooLongError`. Not part of
            the two-argument shape a bare mapping-function name might suggest, because
            `ContextTooLongError` structurally requires it (mirrors `OpenAICompatClient`'s own
            `context_window` field, stored for the same reason).

    Returns:
        `RateLimitedError` for a 429 (with `retry-after` seconds when the header is present and
        numeric); `ProviderUnavailableError` for a connection failure/timeout or any status
        >= 500; `ContextTooLongError` for a 400 whose message names a context-length overflow;
        `ProviderRequestError` for any other 4xx, or for an `AnthropicError` this function does
        not otherwise recognise (a future SDK addition).
    """
    if isinstance(exc, anthropic.RateLimitError):
        return RateLimitedError(provider, retry_after_s=_retry_after_seconds(exc))
    if isinstance(exc, anthropic.APIConnectionError):
        # Covers APITimeoutError too: it subclasses APIConnectionError (both mean "the server
        # never answered", as distinct from a status it did send).
        return ProviderUnavailableError(provider, _describe(exc))
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= UNAVAILABLE_STATUS_FLOOR:
            return ProviderUnavailableError(provider, _describe(exc))
        if isinstance(exc, anthropic.BadRequestError) and _looks_like_context_overflow(exc.message):
            return ContextTooLongError(provider, window=context_window)
        return ProviderRequestError(
            provider, exc.status_code, error_type=exc.type, detail=exc.message
        )
    # SAFETY: an AnthropicError that is neither connection- nor status-shaped (a future SDK
    # addition this module has not special-cased). Still a real failure, so it becomes our
    # generic request error rather than letting a raw vendor exception escape this module.
    return ProviderRequestError(
        provider, UNKNOWN_ERROR_STATUS_CODE, error_type=type(exc).__name__, detail=str(exc)
    )


def _is_degraded_status(exc: anthropic.APIStatusError) -> bool:
    """Return whether `exc` is DEGRADED-shaped for `probe_health`: a 429, or any 5xx status."""
    return isinstance(exc, anthropic.RateLimitError) or exc.status_code >= UNAVAILABLE_STATUS_FLOOR


def _retry_after_seconds(exc: anthropic.RateLimitError) -> float | None:
    """Parse a 429's `retry-after` header as seconds, or None when absent or not a plain number."""
    raw = exc.response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # An HTTP-date form retry-after; not worth parsing for this adapter.


def _looks_like_context_overflow(message: str) -> bool:
    """Return whether a 400's message names a context-length overflow rather than a generic 400."""
    lowered = message.lower()
    return any(marker in lowered for marker in CONTEXT_OVERFLOW_MARKERS)


def _describe(exc: anthropic.APIError) -> str:
    """Render a short description of an SDK exception; never includes the API key.

    See this module's docstring for why: the key lives only in the request header the SDK
    builds internally, never in an exception's own message or body.
    """
    return f"{type(exc).__name__}: {exc}"
