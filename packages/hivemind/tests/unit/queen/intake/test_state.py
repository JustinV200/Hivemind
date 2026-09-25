"""Tests for hivemind.queen.intake.state: every allowed edge passes, every other one raises.

Fits into the Hive:
    Mirrors src/hivemind/queen/intake/state.py (codingrules section 3; section 14.3: "every state
    machine has a test that walks every allowed transition and asserts every forbidden one
    raises").

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.intake.state for the table under test.
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.queen.intake import (
    GOAL_REQUEST_TRANSITIONS,
    TERMINAL_GOAL_REQUEST_STATES,
    GoalRequestState,
    InvalidGoalRequestTransitionError,
    assert_goal_request_transition,
    can_goal_request_transition,
)

_ALLOWED = {
    (GoalRequestState.RECEIVED, GoalRequestState.PLANNING),
    (GoalRequestState.RECEIVED, GoalRequestState.AWAITING_CONFIRMATION),
    (GoalRequestState.RECEIVED, GoalRequestState.REFUSED),
    (GoalRequestState.AWAITING_CONFIRMATION, GoalRequestState.RECEIVED),
    (GoalRequestState.AWAITING_CONFIRMATION, GoalRequestState.REFUSED),
    (GoalRequestState.PLANNING, GoalRequestState.PLANNED),
    (GoalRequestState.PLANNING, GoalRequestState.REFUSED),
}
_EVERY_PAIR = list(itertools.product(GoalRequestState, repeat=2))


def test_the_table_has_one_entry_per_state() -> None:
    assert set(GOAL_REQUEST_TRANSITIONS) == set(GoalRequestState)


def test_the_table_lists_exactly_the_documented_edges() -> None:
    table = {(start, end) for start, ends in GOAL_REQUEST_TRANSITIONS.items() for end in ends}

    assert table == _ALLOWED


def test_the_terminal_states_are_exactly_those_with_no_edge_out() -> None:
    no_edge = {state for state, ends in GOAL_REQUEST_TRANSITIONS.items() if not ends}

    assert no_edge == TERMINAL_GOAL_REQUEST_STATES


@pytest.mark.parametrize(("start", "end"), sorted(_ALLOWED, key=str))
def test_every_allowed_edge_passes(start: GoalRequestState, end: GoalRequestState) -> None:
    assert can_goal_request_transition(start, end)
    assert_goal_request_transition(start, end, "goalreq_x")


@pytest.mark.parametrize(
    ("start", "end"), [pair for pair in _EVERY_PAIR if pair not in _ALLOWED], ids=str
)
def test_every_other_edge_raises(start: GoalRequestState, end: GoalRequestState) -> None:
    assert not can_goal_request_transition(start, end)
    with pytest.raises(InvalidGoalRequestTransitionError) as caught:
        assert_goal_request_transition(start, end, "goalreq_x")
    assert caught.value.from_state == start.value
    assert caught.value.to_state == end.value
