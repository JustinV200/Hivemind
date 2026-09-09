"""Tests for hivemind.supervision.attendant.weights: WeightTable and Priority.

Fits into the Hive:
    Mirrors src/hivemind/supervision/attendant/weights.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.attendant.weights for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.supervision.alarm import AlarmSeverity
from hivemind.supervision.attendant.items import InboxKind
from hivemind.supervision.attendant.weights import Priority, WeightTable


def test_queen_default_ranks_alarm_above_human_message_above_waggle_message() -> None:
    weights = WeightTable.queen_default()

    alarm_and_critical = (
        weights.kind_weights[InboxKind.ALARM] + weights.severity_weights[AlarmSeverity.CRITICAL]
    )
    human_message = weights.kind_weights[InboxKind.HUMAN_MESSAGE]
    waggle_message = weights.kind_weights[InboxKind.WAGGLE_MESSAGE]

    assert alarm_and_critical > human_message > waggle_message


def test_queen_default_has_no_principal_favouritism_by_default() -> None:
    weights = WeightTable.queen_default()

    assert weights.principal_weights == {}


def test_queen_default_covers_every_inbox_kind_and_severity() -> None:
    weights = WeightTable.queen_default()

    assert set(weights.kind_weights) == set(InboxKind)
    assert set(weights.severity_weights) == set(AlarmSeverity)


def test_warden_default_ranks_alarms_and_questions_above_routine_traffic() -> None:
    weights = WeightTable.warden_default()

    routine = {InboxKind.WAGGLE_MESSAGE, InboxKind.WATCH_OBSERVATION, InboxKind.TIMER}
    assert len({weights.kind_weights[kind] for kind in routine}) == 1
    assert weights.kind_weights[InboxKind.ALARM] > weights.kind_weights[InboxKind.QUESTION]
    assert weights.kind_weights[InboxKind.QUESTION] > weights.kind_weights[InboxKind.TIMER]
    assert weights.principal_weights == {}


def test_warden_default_lets_a_critical_alarm_outrank_everything_routine() -> None:
    weights = WeightTable.warden_default()

    critical = (
        weights.kind_weights[InboxKind.ALARM] + weights.severity_weights[AlarmSeverity.CRITICAL]
    )
    assert (
        weights.severity_weights[AlarmSeverity.INFO]
        < weights.severity_weights[AlarmSeverity.WARNING]
    )
    assert (
        weights.severity_weights[AlarmSeverity.WARNING]
        < weights.severity_weights[AlarmSeverity.CRITICAL]
    )
    assert critical > max(
        weights.kind_weights[kind] for kind in InboxKind if kind is not InboxKind.ALARM
    )


def test_weight_table_is_frozen() -> None:
    weights = WeightTable.queen_default()

    with pytest.raises(ValidationError, match="frozen"):
        weights.age_weight_per_s = 1.0  # type: ignore[misc]  # The assignment is the test.


def test_weight_table_rejects_a_negative_age_weight() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        WeightTable(
            kind_weights={},
            principal_weights={},
            age_weight_per_s=-0.1,
            severity_weights={},
            latency_weight=0.0,
            task_link_weight=0.0,
        )


def test_priority_carries_score_and_reasons() -> None:
    priority = Priority(score=4.5, reasons=("kind=ALARM:+10.000",))

    assert priority.score == 4.5
    assert priority.reasons == ("kind=ALARM:+10.000",)


def test_priority_is_frozen() -> None:
    priority = Priority(score=1.0, reasons=())

    with pytest.raises(ValidationError, match="frozen"):
        priority.score = 2.0  # type: ignore[misc]  # The assignment is the test.
