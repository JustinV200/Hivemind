"""Unit tests for hivemind.cell.lease_state: every allowed and forbidden LeaseState transition."""

from __future__ import annotations

import pytest

from hivemind.cell.errors import InvalidLeaseTransitionError
from hivemind.cell.lease_state import TRANSITIONS, LeaseState, assert_transition, can_transition

_ALL_PAIRS = tuple((a, b) for a in LeaseState for b in LeaseState)
_ALLOWED_PAIRS = tuple((a, b) for a, edges in TRANSITIONS.items() for b in edges)
_FORBIDDEN_PAIRS = tuple(pair for pair in _ALL_PAIRS if pair not in _ALLOWED_PAIRS)


def test_transitions_has_exactly_one_entry_per_lease_state() -> None:
    assert set(TRANSITIONS) == set(LeaseState)


def test_released_is_the_only_terminal_state() -> None:
    terminal = {state for state, edges in TRANSITIONS.items() if not edges}

    assert terminal == {LeaseState.RELEASED}


@pytest.mark.parametrize("from_state,to_state", _ALLOWED_PAIRS)
def test_every_allowed_edge_is_permitted(from_state: LeaseState, to_state: LeaseState) -> None:
    assert can_transition(from_state, to_state)
    assert_transition(from_state, to_state)  # Does not raise.


@pytest.mark.parametrize("from_state,to_state", _FORBIDDEN_PAIRS)
def test_every_forbidden_edge_is_rejected(from_state: LeaseState, to_state: LeaseState) -> None:
    assert not can_transition(from_state, to_state)
    with pytest.raises(InvalidLeaseTransitionError):
        assert_transition(from_state, to_state, lease_id="lease_test")
