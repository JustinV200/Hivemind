"""Tests for hivemind.forage.grant_state: GrantState and TRANSITIONS.

Fits into the Hive:
    Mirrors src/hivemind/forage/grant_state.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.grant_state for the module under test.
    - .claude/codingrules.md section 14.3: "Every state machine has a test that walks every
      allowed transition and asserts every forbidden one raises."
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.forage.errors import InvalidGrantTransitionError
from hivemind.forage.grant_state import (
    TERMINAL_STATES,
    TRANSITIONS,
    GrantState,
    assert_transition,
    can_transition,
    is_terminal,
)

# Every (from, to) pair TRANSITIONS allows; used to parametrize the "every edge succeeds" test and
# to build its complement below for the "every non-edge raises" test.
_ALLOWED_EDGES = [(state, target) for state, targets in TRANSITIONS.items() for target in targets]
_ALL_PAIRS = list(itertools.product(GrantState, GrantState))
_FORBIDDEN_EDGES = [pair for pair in _ALL_PAIRS if pair not in _ALLOWED_EDGES]


def test_transitions_has_exactly_one_entry_per_grant_state() -> None:
    assert set(TRANSITIONS.keys()) == set(GrantState)


def test_terminal_states_matches_the_states_with_no_outgoing_edge() -> None:
    empty_edge_states = {state for state, targets in TRANSITIONS.items() if not targets}

    assert empty_edge_states == TERMINAL_STATES


def test_terminal_states_is_exactly_revoked() -> None:
    assert frozenset({GrantState.REVOKED}) == TERMINAL_STATES


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_can_transition_accepts_every_allowed_edge(
    from_state: GrantState, to_state: GrantState
) -> None:
    assert can_transition(from_state, to_state) is True


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_assert_transition_does_not_raise_on_every_allowed_edge(
    from_state: GrantState, to_state: GrantState
) -> None:
    assert_transition(from_state, to_state)


@pytest.mark.parametrize(("from_state", "to_state"), _FORBIDDEN_EDGES)
def test_can_transition_rejects_every_pair_outside_the_table(
    from_state: GrantState, to_state: GrantState
) -> None:
    assert can_transition(from_state, to_state) is False


@pytest.mark.parametrize(("from_state", "to_state"), _FORBIDDEN_EDGES)
def test_assert_transition_raises_on_every_pair_outside_the_table(
    from_state: GrantState, to_state: GrantState
) -> None:
    with pytest.raises(InvalidGrantTransitionError):
        assert_transition(from_state, to_state)


def test_assert_transition_error_carries_the_grant_id_when_given() -> None:
    with pytest.raises(InvalidGrantTransitionError) as excinfo:
        assert_transition(GrantState.REVOKED, GrantState.ACTIVE, subject_id="grant_abc")

    assert excinfo.value.subject_id == "grant_abc"


@pytest.mark.parametrize("state", list(GrantState))
def test_is_terminal_matches_terminal_states(state: GrantState) -> None:
    assert is_terminal(state) == (state in TERMINAL_STATES)


def test_every_terminal_state_has_no_allowed_transitions() -> None:
    for state in TERMINAL_STATES:
        assert TRANSITIONS[state] == frozenset()


def test_exhausted_can_be_revoked_directly() -> None:
    # Appendix C allows EXHAUSTED -> REVOKED: a Warden that dies or drops offline while its grant
    # is spent must be revocable without a pointless top-up first (codingrules 8.10, grants are
    # leases that return to the pool when the holder is dead or offline).
    assert can_transition(GrantState.EXHAUSTED, GrantState.REVOKED) is True


def test_all_pairs_partition_into_allowed_and_forbidden_with_no_overlap() -> None:
    # Sanity check on the fixture-building logic above: every (state, state) pair is in exactly
    # one of the two lists, so the two parametrized tests together cover the full cartesian
    # product with no gap and no double-count.
    assert len(_ALLOWED_EDGES) + len(_FORBIDDEN_EDGES) == len(_ALL_PAIRS)
    assert set(_ALLOWED_EDGES).isdisjoint(_FORBIDDEN_EDGES)
