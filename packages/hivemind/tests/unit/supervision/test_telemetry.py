"""Tests for hivemind.supervision.telemetry: ContextTelemetry re-export and its pure helpers.

Fits into the Hive:
    Mirrors src/hivemind/supervision/telemetry.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.telemetry for the module under test.
"""

from __future__ import annotations

from builders.supervision import make_telemetry

from hivemind.supervision.telemetry import (
    ContextTelemetry,
    fraction_used,
    is_past_threshold,
    summarise,
)
from waggle.messages.supervision import ContextTelemetry as WireContextTelemetry


def test_context_telemetry_is_the_waggle_value_model_not_a_mirror() -> None:
    # codingrules section 6.1: a value model with no behaviour is imported directly, never
    # mirrored, so the two names must be the exact same class object.
    assert ContextTelemetry is WireContextTelemetry


def test_fraction_used_computes_tokens_over_window() -> None:
    telemetry = make_telemetry(tokens_used=2_048, context_window=8_192)

    assert fraction_used(telemetry) == 0.25


def test_fraction_used_can_exceed_one_without_raising() -> None:
    telemetry = make_telemetry(tokens_used=10_000, context_window=8_192)

    assert fraction_used(telemetry) > 1.0


def test_is_past_threshold_true_at_or_above_the_threshold() -> None:
    telemetry = make_telemetry(tokens_used=6_144, context_window=8_192)  # exactly 0.75

    assert is_past_threshold(telemetry, 0.75) is True
    assert is_past_threshold(telemetry, 0.76) is False


def test_summarise_names_the_goal_and_the_fraction_and_never_the_blocker_text() -> None:
    telemetry = make_telemetry(
        goal="Ship the feature",
        tokens_used=4_096,
        context_window=8_192,
        blockers=("waiting on a secret only the operator has",),
    )

    line = summarise(telemetry)

    assert "Ship the feature" in line
    assert "50%" in line
    assert "blockers=1" in line
    # The blocker's own text can describe operator or Real Cell detail (codingrules section 12
    # forbids logging page contents); only its count belongs in a summary line.
    assert "waiting on a secret" not in line
