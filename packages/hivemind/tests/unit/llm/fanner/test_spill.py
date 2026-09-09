"""Tests for hivemind.llm.fanner.spill: SpillReason, static_spill_reason and queue_wait_exceeded.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/spill.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.spill for the module under test.
"""

from __future__ import annotations

from builders.forage import make_source

from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.fanner.spill import (
    SPILL_WAIT_FRACTION,
    SpillReason,
    queue_wait_exceeded,
    static_spill_reason,
)


def test_static_spill_reason_is_none_for_an_unknown_source() -> None:
    tempo = Tempo(accuracy=AccuracyBar.CRITICAL)

    assert static_spill_reason(None, tempo) is None


def test_static_spill_reason_flags_grade_below_the_tempo_floor() -> None:
    source = make_source(grade=1)
    tempo = Tempo(accuracy=AccuracyBar.CRITICAL)  # grade_floor(CRITICAL) == 4

    assert static_spill_reason(source, tempo) is SpillReason.GRADE_BELOW_FLOOR


def test_static_spill_reason_flags_a_source_with_zero_seats_as_not_loaded() -> None:
    source = make_source(grade=5, seats=0)
    tempo = Tempo(accuracy=AccuracyBar.LOW)

    assert static_spill_reason(source, tempo) is SpillReason.MODEL_NOT_LOADED


def test_static_spill_reason_is_none_when_grade_and_seats_both_clear() -> None:
    source = make_source(grade=5, seats=1)
    tempo = Tempo(accuracy=AccuracyBar.CRITICAL)

    assert static_spill_reason(source, tempo) is None


def test_queue_wait_exceeded_is_false_with_no_latency_budget() -> None:
    tempo = Tempo(accuracy=AccuracyBar.NORMAL, latency_budget_s=None)

    assert queue_wait_exceeded(100.0, tempo) is False


def test_queue_wait_exceeded_compares_against_the_configured_fraction() -> None:
    tempo = Tempo(accuracy=AccuracyBar.NORMAL, latency_budget_s=10.0)
    threshold = SPILL_WAIT_FRACTION * 10.0

    assert queue_wait_exceeded(threshold + 0.01, tempo) is True
    assert queue_wait_exceeded(threshold - 0.01, tempo) is False
