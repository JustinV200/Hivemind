"""Unit tests for hivemind.supervision.capping.state: every allowed and forbidden transition."""

from __future__ import annotations

import pytest

from hivemind.supervision.capping.errors import InvalidProposalTransitionError
from hivemind.supervision.capping.state import (
    TRANSITIONS,
    ProposalState,
    assert_transition,
    can_transition,
    is_terminal,
)

_ALL_PAIRS = tuple((a, b) for a in ProposalState for b in ProposalState)
_ALLOWED_PAIRS = tuple((a, b) for a, edges in TRANSITIONS.items() for b in edges)
_FORBIDDEN_PAIRS = tuple(pair for pair in _ALL_PAIRS if pair not in _ALLOWED_PAIRS)


def test_transitions_has_exactly_one_entry_per_proposal_state() -> None:
    assert set(TRANSITIONS) == set(ProposalState)


def test_verified_rejected_and_rolled_back_are_the_only_terminal_states() -> None:
    terminal = {state for state, edges in TRANSITIONS.items() if not edges}

    assert terminal == {ProposalState.VERIFIED, ProposalState.REJECTED, ProposalState.ROLLED_BACK}


@pytest.mark.parametrize("state", list(ProposalState))
def test_is_terminal_matches_the_transitions_table(state: ProposalState) -> None:
    assert is_terminal(state) == (not TRANSITIONS[state])


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_PAIRS)
def test_every_allowed_edge_is_permitted(
    from_state: ProposalState, to_state: ProposalState
) -> None:
    assert can_transition(from_state, to_state)
    assert_transition(from_state, to_state)  # Does not raise.


@pytest.mark.parametrize(("from_state", "to_state"), _FORBIDDEN_PAIRS)
def test_every_forbidden_edge_is_rejected(
    from_state: ProposalState, to_state: ProposalState
) -> None:
    assert not can_transition(from_state, to_state)
    with pytest.raises(InvalidProposalTransitionError):
        assert_transition(from_state, to_state, proposal_id="msg_test")
