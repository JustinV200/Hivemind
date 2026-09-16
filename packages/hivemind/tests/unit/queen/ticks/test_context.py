"""Tests for hivemind.queen.ticks.context: intervention_for.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/context.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.context for the module under test.
"""

from __future__ import annotations

from hivemind.memory.thresholds import Thresholds
from hivemind.queen.ticks.context import intervention_for
from hivemind.supervision import Compact, Handoff
from waggle.messages.supervision import ContextTelemetry

_THRESHOLDS = Thresholds(compact_at=0.5, handoff_threshold=0.8)


def _telemetry(fraction: float) -> ContextTelemetry:
    window = 1_000
    return ContextTelemetry(
        tokens_used=int(window * fraction),
        context_window=window,
        goal="Doing a thing.",
        last_actions=(),
        blockers=(),
        spend=0.0,
    )


def test_intervention_for_returns_none_below_both_thresholds() -> None:
    assert intervention_for(_telemetry(0.1), _THRESHOLDS) is None


def test_intervention_for_returns_compact_past_compact_at() -> None:
    intervention = intervention_for(_telemetry(0.6), _THRESHOLDS)

    assert isinstance(intervention, Compact)
    assert "60%" in intervention.reason


def test_intervention_for_returns_handoff_past_handoff_threshold() -> None:
    intervention = intervention_for(_telemetry(0.9), _THRESHOLDS)

    assert isinstance(intervention, Handoff)
    assert "90%" in intervention.reason
