"""Define LLMError and the LLM boundary's own error tree.

Every ``LLMProvider`` implementation (an adapter that lets the Hive talk to a model, hosted or
local) raises one of these, never a vendor SDK exception, so a caller several layers up can catch
one typed error regardless of which provider is behind the call (codingrules section 8.6: "core
code branches on capabilities, never on provider name" extends to error handling too). Every
subclass carries the ``provider`` name (the manifest's ``[llm.providers.<name>]`` key) so a log
line or a Pheromone Trail payload can identify which binding failed without a stack trace.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Raised by every ``hivemind.llm.providers.*``
    adapter and by ``hivemind.llm.fake.FakeLLMProvider``; caught by the degradation ladders
    (``hivemind.llm.ladders``, a later roadmap step), routing, and Clustering, which pauses
    affected bees on a provider going down with no fallback (codingrules section 8.13).

Key invariants:
    - Every LLMError subclass sets its own `code` and carries `provider`; none shares a code with
      another (codingrules section 10).
    - `MalformedOutputError.message` never repeats the full `raw` text past
      `MAX_RAW_PREVIEW_CHARS`: the full text is still kept on the `raw` attribute for a caller
      that needs to retry or inspect it, but the message itself stays short (codingrules section
      12's "never log ... full page contents" spirit applied to an exception's own message).

See Also:
    - .claude/codingrules.md section 10 for the errors and exceptions rules this module follows.
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules these errors
      support.
    - hivemind.common.errors for HiveMindError, the root every subsystem's tree descends from.
    - hivemind.llm.provider for LLMProvider, whose methods raise these.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import HiveMindError

MAX_RAW_PREVIEW_CHARS = 200  # Enough to see the shape of a malformed reply; never the whole thing.

__all__ = [
    "MAX_RAW_PREVIEW_CHARS",
    "ContextTooLongError",
    "LLMError",
    "MalformedOutputError",
    "OfflineViolationError",
    "ProviderRequestError",
    "ProviderUnavailableError",
    "RateLimitedError",
    "RefusedError",
    "UnknownProviderError",
]


class LLMError(HiveMindError):
    """Root of every error `hivemind.llm` raises on purpose.

    Every subclass carries `provider`, the manifest's `[llm.providers.<name>]` key, so a caller
    can identify which binding failed without parsing a message string.
    """

    code: ClassVar[str] = "hivemind.llm.error"

    def __init__(self, message: str, *, provider: str) -> None:
        """Build the error, recording which provider raised it.

        Args:
            message: A full sentence containing the identifiers needed to debug this failure.
            provider: The manifest's `[llm.providers.<name>]` key for the provider that failed.
        """
        super().__init__(message)
        self.provider = provider


class RateLimitedError(LLMError):
    """Raise when a provider refuses a call because a rate limit was exceeded."""

    code: ClassVar[str] = "hivemind.llm.rate_limited"

    def __init__(self, provider: str, retry_after_s: float | None = None) -> None:
        """Build the error for a rate-limited call.

        Args:
            provider: The provider that rate-limited the call.
            retry_after_s: How long the provider asked the caller to wait, if it said so.
        """
        # The retry hint is folded into the sentence only when the provider actually gave one, so
        # the message never implies a number that was not really there.
        wait_clause = f"; retry after {retry_after_s}s" if retry_after_s is not None else ""
        super().__init__(
            f"Provider {provider!r} rate-limited this call{wait_clause}.", provider=provider
        )
        self.retry_after_s = retry_after_s


class ProviderUnavailableError(LLMError):
    """Raise when a provider cannot be reached or is refusing every call right now."""

    code: ClassVar[str] = "hivemind.llm.provider_unavailable"

    def __init__(self, provider: str, detail: str = "the provider is unavailable") -> None:
        """Build the error for an unreachable or refusing provider.

        Args:
            provider: The provider that is unavailable.
            detail: A short reason, e.g. a connection failure summary or, for
                `hivemind.llm.fake.FakeLLMProvider`, "the scripted response queue ran dry".
        """
        super().__init__(f"Provider {provider!r} is unavailable: {detail}.", provider=provider)


class ContextTooLongError(LLMError):
    """Raise when a request's content exceeds the bound model's context window."""

    code: ClassVar[str] = "hivemind.llm.context_too_long"

    def __init__(
        self,
        provider: str,
        window: int,
        requested: int | None = None,
        suggested_max_input: int | None = None,
    ) -> None:
        """Build the error for a request that overflows the context window.

        Args:
            provider: The provider that rejected the call.
            window: The model's context window, in tokens.
            requested: The request's estimated token count, when known.
            suggested_max_input: A caller-facing suggestion for a smaller input budget, when the
                provider or a token counter can compute one.
        """
        requested_clause = f", requested ~{requested}" if requested is not None else ""
        super().__init__(
            f"Provider {provider!r}'s context window is {window} tokens{requested_clause}; "
            "the request does not fit.",
            provider=provider,
        )
        self.window = window
        self.requested = requested
        self.suggested_max_input = suggested_max_input


class RefusedError(LLMError):
    """Raise when a model declines to answer (StopReason.REFUSAL) rather than erroring."""

    code: ClassVar[str] = "hivemind.llm.refused"

    def __init__(self, provider: str, reason: str = "no reason given") -> None:
        """Build the error for a refused call.

        Args:
            provider: The provider whose model refused.
            reason: The provider's own stated reason, when it gave one.
        """
        super().__init__(f"Provider {provider!r} refused this call: {reason}.", provider=provider)
        self.reason = reason


class MalformedOutputError(LLMError):
    """Raise when a structured or tool-call ladder exhausts its retries on unparseable output."""

    code: ClassVar[str] = "hivemind.llm.malformed_output"

    def __init__(self, provider: str, raw: str, attempts: int) -> None:
        """Build the error for output that never became parseable.

        Args:
            provider: The provider whose output could not be parsed or validated.
            raw: The last raw response text. Kept in full on `self.raw` for a caller that wants
                to inspect or log it under its own retention rules; the message itself only
                previews the first `MAX_RAW_PREVIEW_CHARS` characters.
            attempts: How many attempts the ladder made before giving up.
        """
        preview = raw if len(raw) <= MAX_RAW_PREVIEW_CHARS else raw[:MAX_RAW_PREVIEW_CHARS] + "..."
        super().__init__(
            f"Provider {provider!r} produced unparseable output after {attempts} attempt(s): "
            f"{preview!r}",
            provider=provider,
        )
        self.raw = raw
        self.attempts = attempts


class UnknownProviderError(LLMError):
    """Raise when a slot names a provider that has no `[llm.providers.<name>]` entry."""

    code: ClassVar[str] = "hivemind.llm.unknown_provider"

    def __init__(self, provider: str) -> None:
        """Build the error for a manifest reference to a provider that does not exist.

        Args:
            provider: The provider name that was looked up and not found.
        """
        super().__init__(f"No provider named {provider!r} is registered.", provider=provider)


class ProviderRequestError(LLMError):
    """Raise for a provider's non-retryable 4xx rejection of the request itself.

    Covers every 4xx a more specific type does not already claim: `RateLimitedError` owns 429,
    and `ContextTooLongError` owns the 400s whose message is about context length specifically.
    Everything else in the 4xx family (401, 403, 404, a plain 400 that is not about context
    length, 413, 422, ...) lands here, because the request was rejected on its own terms and
    retrying it unchanged will not help -- unlike a rate limit (wait) or an outage (try later).
    An OpenAI-compatible server's own probe-time model refusal (`OpenAICompatProvider.probe`,
    hivemind.llm.providers.openai_compat) also raises this, synthesizing a 404-shaped status
    since no real HTTP response is involved. A later dispatch's Anthropic adapter raises the same
    type for its own equivalent 4xx family (codingrules section 8.6: one error tree at the
    boundary), so a caller catches one
    type regardless of which adapter is behind the call.
    """

    code: ClassVar[str] = "hivemind.llm.provider_request"

    def __init__(
        self, provider: str, status_code: int, error_type: str | None = None, detail: str = ""
    ) -> None:
        """Build the error for a provider's non-retryable 4xx.

        Args:
            provider: The provider that rejected the request.
            status_code: The HTTP status code the provider responded with (or a synthesized one,
                documented by the raising call site, when no real HTTP response exists).
            error_type: The provider's own error-type string, when its error body names one
                (OpenAI-compatible servers commonly nest one at `error.type`).
            detail: A short, human-readable reason folded into the message; not kept as an
                attribute (mirrors `ProviderUnavailableError`'s `detail` parameter), because the
                two stable, structured fields a caller can branch on are `status_code` and
                `error_type`.
        """
        type_clause = f" ({error_type})" if error_type is not None else ""
        detail_clause = f": {detail}" if detail else ""
        super().__init__(
            f"Provider {provider!r} rejected the request with HTTP "
            f"{status_code}{type_clause}{detail_clause}.",
            provider=provider,
        )
        self.status_code = status_code
        self.error_type = error_type


class OfflineViolationError(LLMError):
    """Raise when `[llm] offline = true` would send a call to a non-loopback provider."""

    code: ClassVar[str] = "hivemind.llm.offline_violation"

    def __init__(self, provider: str, base_url: str) -> None:
        """Build the error for a provider that would break offline mode.

        Args:
            provider: The provider that was refused.
            base_url: The non-loopback URL that triggered the refusal.
        """
        super().__init__(
            f"[llm] offline=true refuses provider {provider!r} at non-loopback "
            f"base_url {base_url!r}.",
            provider=provider,
        )
        self.base_url = base_url
