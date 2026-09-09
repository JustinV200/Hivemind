"""Tests for hivemind.llm.errors: LLMError and its subclass tree.

Fits into the Hive:
    Mirrors src/hivemind/llm/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.errors for the module under test.
"""

from __future__ import annotations

from hivemind.common.errors import HiveMindError
from hivemind.llm.errors import (
    MAX_RAW_PREVIEW_CHARS,
    ContextTooLongError,
    LLMError,
    MalformedOutputError,
    OfflineViolationError,
    ProviderUnavailableError,
    RateLimitedError,
    RefusedError,
    UnknownProviderError,
)

ALL_ERROR_CLASSES = (
    LLMError,
    RateLimitedError,
    ProviderUnavailableError,
    ContextTooLongError,
    RefusedError,
    MalformedOutputError,
    UnknownProviderError,
    OfflineViolationError,
)


def test_every_llm_error_subclasses_llm_error_and_hive_mind_error() -> None:
    for error_class in ALL_ERROR_CLASSES:
        assert issubclass(error_class, LLMError)
        assert issubclass(error_class, HiveMindError)


def test_every_llm_error_has_a_unique_code() -> None:
    codes = [error_class.code for error_class in ALL_ERROR_CLASSES]

    assert len(codes) == len(set(codes))


def test_llm_error_carries_the_provider_it_was_built_with() -> None:
    error = LLMError("something went wrong", provider="anthropic")

    assert error.provider == "anthropic"
    assert "something went wrong" in str(error)


def test_rate_limited_error_mentions_the_provider_and_retry_hint() -> None:
    error = RateLimitedError("anthropic", retry_after_s=12.5)

    assert error.provider == "anthropic"
    assert error.retry_after_s == 12.5
    assert "anthropic" in str(error)
    assert "12.5" in str(error)


def test_rate_limited_error_omits_the_retry_clause_when_not_given() -> None:
    error = RateLimitedError("anthropic")

    assert error.retry_after_s is None
    assert "retry after" not in str(error)


def test_provider_unavailable_error_mentions_the_provider_and_detail() -> None:
    error = ProviderUnavailableError("local", "connection refused")

    assert error.provider == "local"
    assert "local" in str(error)
    assert "connection refused" in str(error)


def test_context_too_long_error_carries_window_requested_and_suggestion() -> None:
    error = ContextTooLongError(
        "anthropic", window=8_192, requested=9_000, suggested_max_input=7_000
    )

    assert error.window == 8_192
    assert error.requested == 9_000
    assert error.suggested_max_input == 7_000
    assert "8192" in str(error)
    assert "9000" in str(error)


def test_context_too_long_error_omits_the_requested_clause_when_not_given() -> None:
    error = ContextTooLongError("anthropic", window=8_192)

    assert error.requested is None
    assert "requested" not in str(error)


def test_refused_error_mentions_the_reason() -> None:
    error = RefusedError("anthropic", "content policy")

    assert error.reason == "content policy"
    assert "content policy" in str(error)


def test_malformed_output_error_keeps_the_full_raw_text_but_truncates_the_message() -> None:
    raw = "x" * (MAX_RAW_PREVIEW_CHARS + 50)

    error = MalformedOutputError("local", raw=raw, attempts=3)

    assert error.raw == raw  # The full text is kept for a caller that wants to retry or inspect.
    assert error.attempts == 3
    assert raw not in str(error)  # The message previews it, but never repeats it in full.


def test_unknown_provider_error_names_the_provider() -> None:
    error = UnknownProviderError("ghost")

    assert error.provider == "ghost"
    assert "ghost" in str(error)


def test_offline_violation_error_names_the_provider_and_base_url() -> None:
    error = OfflineViolationError("hosted", "https://api.example.com")

    assert error.provider == "hosted"
    assert error.base_url == "https://api.example.com"
    assert "https://api.example.com" in str(error)
