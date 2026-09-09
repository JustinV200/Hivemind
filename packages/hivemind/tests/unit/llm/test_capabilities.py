"""Tests for hivemind.llm.capabilities: ProviderCapabilities, HealthState and ProviderHealth.

Fits into the Hive:
    Mirrors src/hivemind/llm/capabilities.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.capabilities for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.llm.capabilities import (
    FULL_CONTEXT_WINDOW_DEFAULT,
    NONE_CONTEXT_WINDOW_DEFAULT,
    HealthState,
    ProviderCapabilities,
    ProviderHealth,
)
from waggle.clock import FakeClock

# ──────────────────────────────────────────────────────────────────────────────
# ProviderCapabilities
# ──────────────────────────────────────────────────────────────────────────────


def test_full_enables_every_capability_at_the_default_window() -> None:
    capabilities = ProviderCapabilities.full()

    assert capabilities.context_window == FULL_CONTEXT_WINDOW_DEFAULT
    assert all(
        getattr(capabilities, field)
        for field in (
            "native_tool_calls",
            "schema_output",
            "json_mode",
            "vision",
            "streaming",
            "reasoning_control",
            "system_role",
            "parallel_tool_calls",
            "token_counting",
        )
    )


def test_none_disables_every_capability_at_the_default_window() -> None:
    capabilities = ProviderCapabilities.none()

    assert capabilities.context_window == NONE_CONTEXT_WINDOW_DEFAULT
    assert not any(
        getattr(capabilities, field)
        for field in (
            "native_tool_calls",
            "schema_output",
            "json_mode",
            "vision",
            "streaming",
            "reasoning_control",
            "system_role",
            "parallel_tool_calls",
            "token_counting",
        )
    )


def test_full_and_none_accept_a_context_window_override() -> None:
    assert ProviderCapabilities.full(context_window=50_000).context_window == 50_000
    assert ProviderCapabilities.none(context_window=4_096).context_window == 4_096


def test_provider_capabilities_rejects_a_non_positive_context_window() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        ProviderCapabilities.full(context_window=0)


def test_provider_capabilities_rejects_an_unknown_field() -> None:
    payload = {**ProviderCapabilities.full().model_dump(), "bogus": True}

    with pytest.raises(ValidationError, match="extra"):
        ProviderCapabilities.model_validate(payload)


def test_provider_capabilities_round_trips_through_json() -> None:
    capabilities = ProviderCapabilities.full(context_window=12_345)

    restored = ProviderCapabilities.model_validate_json(capabilities.model_dump_json())

    assert restored == capabilities


def test_provider_capabilities_is_frozen() -> None:
    capabilities = ProviderCapabilities.full()

    with pytest.raises(ValidationError, match="frozen"):
        capabilities.vision = False  # type: ignore[misc]  # The assignment is the test.


# ──────────────────────────────────────────────────────────────────────────────
# HealthState / ProviderHealth
# ──────────────────────────────────────────────────────────────────────────────


def test_health_state_has_exactly_the_three_documented_members() -> None:
    assert [member.name for member in HealthState] == ["HEALTHY", "DEGRADED", "DOWN"]


def test_provider_health_round_trips_through_json() -> None:
    clock = FakeClock()
    health = ProviderHealth(state=HealthState.DEGRADED, detail="slow", checked_at=clock.now())

    restored = ProviderHealth.model_validate_json(health.model_dump_json())

    assert restored == health


def test_provider_health_rejects_an_unknown_field() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="extra"):
        ProviderHealth.model_validate(
            {"state": "HEALTHY", "detail": "ok", "checked_at": clock.now(), "bogus": 1}
        )
