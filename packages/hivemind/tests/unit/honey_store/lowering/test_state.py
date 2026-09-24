"""Tests for hivemind.honey_store.lowering.state: a lowering proposal's one transition table.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/lowering/state.py (codingrules section 3). Walks every allowed
    edge for every approver it names, and asserts every other (state, state, approver) triple is
    refused (codingrules 14.3: every forbidden transition raises).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.lowering.state for the module under test.
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.errors import LoweringTransitionError
from hivemind.honey_store.lowering.state import (
    TERMINAL_STATES,
    TRANSITIONS,
    LoweringState,
    assert_transition,
    can_transition,
)

_PROPOSED = LoweringState.PROPOSED
_LOWERED = LoweringState.LOWERED
_REJECTED = LoweringState.REJECTED
_JUDGE = LabelApprover.JUDGE
_HUMAN = LabelApprover.HUMAN

# ADR-0034's three edges, spelled out independently of the table under test.
_ALLOWED = {
    (_PROPOSED, _LOWERED, _JUDGE),
    (_PROPOSED, _LOWERED, _HUMAN),
    (_PROPOSED, _REJECTED, _JUDGE),
    (_PROPOSED, _REJECTED, _HUMAN),
    (_REJECTED, _LOWERED, _HUMAN),
}
_EVERY_TRIPLE = list(itertools.product(LoweringState, LoweringState, LabelApprover))


def test_transitions_has_one_entry_per_state() -> None:
    assert set(TRANSITIONS) == set(LoweringState)


def test_terminal_states_are_exactly_the_states_with_no_edge() -> None:
    assert {state for state, edges in TRANSITIONS.items() if not edges} == TERMINAL_STATES
    assert {_LOWERED} == TERMINAL_STATES


@pytest.mark.parametrize(("current", "target", "approver"), sorted(_ALLOWED, key=str))
def test_assert_transition_allows_every_listed_edge(
    current: LoweringState, target: LoweringState, approver: LabelApprover
) -> None:
    assert can_transition(current, target, approver)

    assert_transition(current, target, approver, "lowering_x")


@pytest.mark.parametrize(
    ("current", "target", "approver"),
    [triple for triple in _EVERY_TRIPLE if triple not in _ALLOWED],
)
def test_assert_transition_refuses_every_other_edge(
    current: LoweringState, target: LoweringState, approver: LabelApprover
) -> None:
    assert not can_transition(current, target, approver)

    with pytest.raises(LoweringTransitionError) as caught:
        assert_transition(current, target, approver, "lowering_x")

    assert caught.value.current == current.name
    assert caught.value.target == target.name
    assert caught.value.approver == approver.name


def test_only_the_human_lowers_a_rejected_proposal() -> None:
    # The judge is asked once per Nectar and never overturns its own rejection.
    assert can_transition(_REJECTED, _LOWERED, _HUMAN)
    assert not can_transition(_REJECTED, _LOWERED, _JUDGE)


def test_assert_transition_names_the_proposal_when_given_one() -> None:
    with pytest.raises(LoweringTransitionError, match="lowering_abc cannot move from LOWERED"):
        assert_transition(_LOWERED, _REJECTED, _HUMAN, "lowering_abc")


def test_assert_transition_still_explains_itself_without_a_proposal_id() -> None:
    with pytest.raises(LoweringTransitionError, match="A proposal cannot move from REJECTED"):
        assert_transition(_REJECTED, _REJECTED, _HUMAN)
