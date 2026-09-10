"""Tests for hivemind.queen.autopilot.effort.effort_for.

Fits into the Hive:
    Mirrors src/hivemind/queen/autopilot/effort.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.autopilot.effort for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.forage.slots import Effort
from hivemind.queen.autopilot import effort_for
from hivemind.supervision.attendant import InboxKind


def test_alarm_gets_high_effort() -> None:
    assert effort_for(InboxKind.ALARM) is Effort.HIGH


def test_question_gets_medium_effort() -> None:
    assert effort_for(InboxKind.QUESTION) is Effort.MEDIUM


@pytest.mark.parametrize(
    "kind",
    [
        InboxKind.WAGGLE_MESSAGE,
        InboxKind.HUMAN_MESSAGE,
        InboxKind.TIMER,
        InboxKind.WATCH_OBSERVATION,
    ],
)
def test_everything_else_gets_low_effort(kind: InboxKind) -> None:
    assert effort_for(kind) is Effort.LOW
