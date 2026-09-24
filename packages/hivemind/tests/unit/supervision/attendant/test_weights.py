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
from hivemind.supervision.attendant.weights import (
    GUARD_PRINCIPAL,
    GUARD_REQUEST_WEIGHT,
    Priority,
    WeightTable,
)


def test_queen_default_ranks_alarm_above_human_message_above_waggle_message() -> None:
    weights = WeightTable.queen_default()

    alarm_and_critical = (
        weights.kind_weights[InboxKind.ALARM] + weights.severity_weights[AlarmSeverity.CRITICAL]
    )
    human_message = weights.kind_weights[InboxKind.HUMAN_MESSAGE]
    waggle_message = weights.kind_weights[InboxKind.WAGGLE_MESSAGE]

    assert alarm_and_critical > human_message > waggle_message


def test_queen_default_names_only_the_guard_principal_and_favours_nobody() -> None:
    weights = WeightTable.queen_default()

    # ADR-0035: the Guard principal's multiplier is named, so it can be tuned; neutral as shipped.
    assert weights.principal_weights == {GUARD_PRINCIPAL: 1.0}


def test_queen_default_puts_a_guard_request_above_every_alarm_and_human_message() -> None:
    weights = WeightTable.queen_default()

    critical_alarm = (
        weights.kind_weights[InboxKind.ALARM]
        + weights.severity_weights[AlarmSeverity.CRITICAL]
        + weights.task_link_weight
    )
    human_message = weights.kind_weights[InboxKind.HUMAN_MESSAGE] + weights.task_link_weight

    assert weights.kind_weights[InboxKind.GUARD_REQUEST] == GUARD_REQUEST_WEIGHT == 100.0
    assert GUARD_REQUEST_WEIGHT > critical_alarm > human_message
    assert not weights.refuses(InboxKind.GUARD_REQUEST)


def test_warden_default_refuses_a_guard_request_and_gives_it_no_weight() -> None:
    weights = WeightTable.warden_default()

    assert weights.refuses(InboxKind.GUARD_REQUEST)
    assert InboxKind.GUARD_REQUEST not in weights.kind_weights


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
        weight for kind, weight in weights.kind_weights.items() if kind is not InboxKind.ALARM
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
