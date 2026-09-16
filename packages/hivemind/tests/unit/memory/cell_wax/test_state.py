"""Tests for hivemind.memory.cell_wax.state: WaxState and TRANSITIONS.

Fits into the Hive:
    Mirrors src/hivemind/memory/cell_wax/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.cell_wax.state for the module under test.
    - .claude/codingrules.md section 14.3: "Every state machine has a test that walks every
      allowed transition and asserts every forbidden one raises."
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.memory.cell_wax.state import (
    TERMINAL_STATES,
    TRANSITIONS,
    WaxState,
    assert_transition,
    can_transition,
    is_terminal,
)
from hivemind.memory.errors import InvalidWaxTransitionError

_ALLOWED_EDGES = [(state, target) for state, targets in TRANSITIONS.items() for target in targets]
_ALL_PAIRS = list(itertools.product(WaxState, WaxState))
_FORBIDDEN_EDGES = [pair for pair in _ALL_PAIRS if pair not in _ALLOWED_EDGES]


def test_transitions_has_exactly_one_entry_per_wax_state() -> None:
    assert set(TRANSITIONS.keys()) == set(WaxState)


def test_terminal_states_matches_the_states_with_no_outgoing_edge() -> None:
    empty_edge_states = {state for state, targets in TRANSITIONS.items() if not targets}

    assert empty_edge_states == TERMINAL_STATES


def test_terminal_states_is_exactly_rejected_cleared_expired() -> None:
    assert frozenset({WaxState.REJECTED, WaxState.CLEARED, WaxState.EXPIRED}) == TERMINAL_STATES


def test_allowed_edges_match_appendix_c_verbatim() -> None:
    assert set(_ALLOWED_EDGES) == {
        (WaxState.PROPOSED, WaxState.WRITTEN),
        (WaxState.PROPOSED, WaxState.REJECTED),
        (WaxState.WRITTEN, WaxState.CLEARED),
        (WaxState.WRITTEN, WaxState.EXPIRED),
    }


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_can_transition_accepts_every_allowed_edge(
    from_state: WaxState, to_state: WaxState
) -> None:
    assert can_transition(from_state, to_state) is True


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_assert_transition_does_not_raise_on_every_allowed_edge(
    from_state: WaxState, to_state: WaxState
) -> None:
    assert_transition(from_state, to_state)


@pytest.mark.parametrize(("from_state", "to_state"), _FORBIDDEN_EDGES)
def test_can_transition_rejects_every_pair_outside_the_table(
    from_state: WaxState, to_state: WaxState
) -> None:
    assert can_transition(from_state, to_state) is False


@pytest.mark.parametrize(("from_state", "to_state"), _FORBIDDEN_EDGES)
def test_assert_transition_raises_on_every_pair_outside_the_table(
    from_state: WaxState, to_state: WaxState
) -> None:
    with pytest.raises(InvalidWaxTransitionError):
        assert_transition(from_state, to_state)


def test_is_terminal_matches_terminal_states() -> None:
    for state in WaxState:
        assert is_terminal(state) is (state in TERMINAL_STATES)


def test_assert_transition_folds_subject_id_into_the_error_message() -> None:
    with pytest.raises(InvalidWaxTransitionError, match="wax_test123"):
        assert_transition(WaxState.REJECTED, WaxState.WRITTEN, subject_id="wax_test123")
