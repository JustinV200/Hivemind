"""Tests for hivemind.memory.thresholds.rules: intervention_kind_for and capped_compact_view.

Fits into the Hive:
    Mirrors src/hivemind/memory/thresholds/rules.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.thresholds.rules for the module under test.
"""

from __future__ import annotations

from hivemind.memory.thresholds import (
    MAX_COMPACT_VIEW_CHARS,
    InterventionKind,
    Thresholds,
    capped_compact_view,
    intervention_kind_for,
)
from waggle.messages.supervision import ContextTelemetry

_THRESHOLDS = Thresholds(compact_at=0.5, handoff_threshold=0.8)


def _telemetry(fraction: float, **overrides: object) -> ContextTelemetry:
    window = 1_000
    fields: dict[str, object] = {
        "tokens_used": int(window * fraction),
        "context_window": window,
        "goal": "Do the thing.",
        "last_actions": (),
        "blockers": (),
        "spend": 0.0,
    }
    fields.update(overrides)
    return ContextTelemetry(**fields)


def test_intervention_kind_for_is_none_below_both_thresholds() -> None:
    assert intervention_kind_for(_telemetry(0.2), _THRESHOLDS) is None


def test_intervention_kind_for_is_compact_between_the_two_thresholds() -> None:
    assert intervention_kind_for(_telemetry(0.6), _THRESHOLDS) is InterventionKind.COMPACT


def test_intervention_kind_for_is_handoff_at_or_past_the_handoff_threshold() -> None:
    assert intervention_kind_for(_telemetry(0.8), _THRESHOLDS) is InterventionKind.HANDOFF
    assert intervention_kind_for(_telemetry(0.95), _THRESHOLDS) is InterventionKind.HANDOFF


def test_intervention_kind_for_prefers_handoff_when_both_thresholds_are_crossed() -> None:
    # A fraction past both thresholds must resolve to the harder lever, never the milder one.
    tight = Thresholds(compact_at=0.1, handoff_threshold=0.2)
    assert intervention_kind_for(_telemetry(0.9), tight) is InterventionKind.HANDOFF


def test_capped_compact_view_stays_well_under_the_character_cap() -> None:
    telemetry = _telemetry(
        0.5,
        goal="x" * 999,  # Under ContextTelemetry's own MAX_GOAL_CHARS (1_000).
        last_actions=tuple("a" * 199 for _ in range(10)),  # Under MAX_ACTION_CHARS (200).
        blockers=tuple("b" * 499 for _ in range(10)),  # Under MAX_BLOCKER_CHARS (500).
    )

    view = capped_compact_view(telemetry)

    total = len(view.goal) + len(view.progress) + sum(len(d) for d in view.decisions)
    total += sum(len(t) for t in view.open_threads)
    assert total <= MAX_COMPACT_VIEW_CHARS
    assert view.goal.endswith("...")
    assert len(view.decisions) <= 4
    assert len(view.open_threads) <= 4


def test_capped_compact_view_leaves_short_fields_untouched() -> None:
    telemetry = _telemetry(0.1, goal="short goal", last_actions=("one",), blockers=())

    view = capped_compact_view(telemetry)

    assert view.goal == "short goal"
    assert view.decisions == ("one",)
