"""Tests for hivemind.llm.providers.openai_compat.rate_limit: rate_limit_from_headers.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/rate_limit.py (codingrules section 3:
    tests/unit mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.rate_limit for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hivemind.llm.models import RateLimitSnapshot
from hivemind.llm.providers.openai_compat.rate_limit import rate_limit_from_headers

_NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_rate_limit_from_headers_reads_remaining_counts_and_reset_durations() -> None:
    headers = {
        "x-ratelimit-remaining-requests": "42",
        "x-ratelimit-remaining-tokens": "1000",
        "x-ratelimit-reset-requests": "1s",
        "x-ratelimit-reset-tokens": "6m0s",
    }

    snapshot = rate_limit_from_headers(headers, _NOW)

    assert snapshot == RateLimitSnapshot(
        requests_remaining=42,
        tokens_remaining=1_000,
        requests_reset_at=datetime(2026, 9, 15, 12, 0, 1, tzinfo=UTC),
        tokens_reset_at=datetime(2026, 9, 15, 12, 6, tzinfo=UTC),
    )


def test_rate_limit_from_headers_is_none_when_the_server_sent_none() -> None:
    # Every local server (Ollama, vLLM, llama.cpp, LM Studio) omits all four headers.
    assert rate_limit_from_headers({}, _NOW) is None


def test_rate_limit_from_headers_reads_only_requests_when_tokens_is_absent() -> None:
    snapshot = rate_limit_from_headers({"x-ratelimit-remaining-requests": "5"}, _NOW)

    assert snapshot == RateLimitSnapshot(requests_remaining=5)


def test_rate_limit_from_headers_reads_a_bare_seconds_reset_value() -> None:
    headers = {"x-ratelimit-remaining-requests": "5", "x-ratelimit-reset-requests": "30"}

    snapshot = rate_limit_from_headers(headers, _NOW)

    assert snapshot is not None
    assert snapshot.requests_reset_at == datetime(2026, 9, 15, 12, 0, 30, tzinfo=UTC)


def test_rate_limit_from_headers_reads_an_hours_minutes_seconds_duration() -> None:
    headers = {"x-ratelimit-remaining-requests": "5", "x-ratelimit-reset-requests": "1h2m3s"}

    snapshot = rate_limit_from_headers(headers, _NOW)

    assert snapshot is not None
    assert snapshot.requests_reset_at == datetime(2026, 9, 15, 13, 2, 3, tzinfo=UTC)


def test_rate_limit_from_headers_ignores_an_unparseable_reset_duration() -> None:
    headers = {
        "x-ratelimit-remaining-requests": "5",
        "x-ratelimit-reset-requests": "not-a-duration",
    }

    snapshot = rate_limit_from_headers(headers, _NOW)

    assert snapshot is not None
    assert snapshot.requests_reset_at is None
