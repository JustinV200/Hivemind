"""Tests for hivemind.memory.relevance: RelevanceScore, Scorable, score, item_id, item_timestamp.

Fits into the Hive:
    Mirrors src/hivemind/memory/relevance.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.relevance for the module under test.
    - .claude/roadmap.md step 4.1 for the four property tests this module implements verbatim.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.memory import (
    make_alarm_summary,
    make_decision_summary,
    make_note,
    make_pin,
    make_question_summary,
    make_task_summary,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.memory.relevance import Scorable, item_id, item_timestamp, score
from waggle.clock import FakeClock
from waggle.ids import TaskId

# Generous but bounded: a month of possible ages is enough to exercise decay without hypothesis
# wasting time on implausible multi-year gaps. No per-test deadline: a slow CI host should never
# turn a correct property test flaky (matches tests/unit/guard/test_access.py's own settings).
_SETTINGS = settings(max_examples=100, deadline=None)
_AGE_SECONDS = st.floats(
    min_value=0, max_value=30 * 24 * 3600, allow_nan=False, allow_infinity=False
)


def _every_non_pin_kind(clock: FakeClock) -> list[Scorable]:
    """Build one instance of every Scorable kind that can meaningfully be "unpinned"."""
    return [
        make_task_summary(clock=clock),
        make_alarm_summary(clock=clock),
        make_question_summary(clock=clock),
        make_decision_summary(clock=clock),
        make_note(clock=clock),
    ]


def _every_kind(clock: FakeClock) -> list[Scorable]:
    """Build one instance of every Scorable kind, Pin included."""
    return [*_every_non_pin_kind(clock), make_pin(clock=clock)]


@given(age1=_AGE_SECONDS, age2=_AGE_SECONDS, kind_index=st.integers(min_value=0, max_value=5))
@_SETTINGS
def test_score_is_monotone_non_increasing_in_age(age1: float, age2: float, kind_index: int) -> None:
    younger_age, older_age = sorted((age1, age2))
    clock = FakeClock()
    item = _every_kind(clock)[kind_index]
    base = item_timestamp(item)

    younger_score = score(item, base + timedelta(seconds=younger_age), frozenset(), frozenset())
    older_score = score(item, base + timedelta(seconds=older_age), frozenset(), frozenset())

    assert older_score.value <= younger_score.value


@given(kind_index=st.integers(min_value=0, max_value=4))
@_SETTINGS
def test_pinned_item_always_outscores_the_same_item_unpinned(kind_index: int) -> None:
    clock = FakeClock()
    item = _every_non_pin_kind(clock)[kind_index]
    now = item_timestamp(item)

    unpinned = score(item, now, frozenset(), frozenset())
    pinned = score(item, now, frozenset(), frozenset({item_id(item)}))

    assert pinned.value > unpinned.value


@pytest.mark.parametrize(
    ("lower", "higher"), [("INFO", "WARNING"), ("WARNING", "CRITICAL"), ("INFO", "CRITICAL")]
)
def test_higher_alarm_severity_never_scores_lower(lower: str, higher: str) -> None:
    clock = FakeClock()
    now = clock.now()
    # Same everything but severity, so severity is the only thing that can move the score.
    lower_alarm = make_alarm_summary(clock=clock, severity=lower)
    higher_alarm = make_alarm_summary(clock=clock, severity=higher, id=lower_alarm.id)

    lower_score = score(lower_alarm, now, frozenset(), frozenset())
    higher_score = score(higher_alarm, now, frozenset(), frozenset())

    assert higher_score.value >= lower_score.value


@given(kind_index=st.integers(min_value=0, max_value=4))
@_SETTINGS
def test_task_linkage_never_lowers_a_score(kind_index: int) -> None:
    clock = FakeClock()
    item = _every_non_pin_kind(clock)[kind_index]
    now = item_timestamp(item)
    linking_task = _task_id_of(item)

    without_linkage = score(item, now, frozenset(), frozenset())
    with_linkage = score(item, now, frozenset({linking_task}), frozenset())

    assert with_linkage.value >= without_linkage.value


def _task_id_of(item: Scorable) -> TaskId:
    """Return the task id that would link `item`, so the linkage bonus has something to grant.

    DecisionSummary and Note carry no task_id field; a made-up id is harmless for them since
    `_linkage_bonus` never inspects `active_tasks` for those types anyway (see relevance.py).
    """
    task_id = getattr(item, "task_id", None) or getattr(item, "id", None) or "task_placeholder"
    return TaskId(task_id)


def test_a_pin_is_always_pinned_regardless_of_the_pins_set() -> None:
    clock = FakeClock()
    pin = make_pin(clock=clock)
    now = pin.created_at

    with_empty_pins = score(pin, now, frozenset(), frozenset())
    with_unrelated_pins = score(pin, now, frozenset(), frozenset({"something_else"}))

    # A Pin never needs its own id in `pins` to count as pinned (isinstance check in relevance.py).
    assert with_empty_pins.value == with_unrelated_pins.value


def test_item_id_and_item_timestamp_cover_every_scorable_kind() -> None:
    clock = FakeClock()
    for item in _every_kind(clock):
        # Every kind must answer both without raising; DecisionSummary's id is named differently.
        assert item_id(item) is not None
        assert item_timestamp(item) is not None
