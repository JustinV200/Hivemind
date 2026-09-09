"""Unit tests for hivemind.workers.state.

Covers every allowed and forbidden WorkerState transition, and the mirror sync with
waggle.messages.supervision.WorkerState.
"""

from __future__ import annotations

import pytest

from hivemind.workers.errors import InvalidWorkerTransitionError
from hivemind.workers.state import (
    TRANSITIONS,
    WorkerState,
    assert_transition,
    can_transition,
    is_terminal,
)
from waggle.messages.supervision import WorkerState as WireWorkerState

_ALL_PAIRS = tuple((a, b) for a in WorkerState for b in WorkerState)
_ALLOWED_PAIRS = tuple((a, b) for a, edges in TRANSITIONS.items() for b in edges)
_FORBIDDEN_PAIRS = tuple(pair for pair in _ALL_PAIRS if pair not in _ALLOWED_PAIRS)


def test_transitions_has_exactly_one_entry_per_worker_state() -> None:
    assert set(TRANSITIONS) == set(WorkerState)


def test_done_failed_and_killed_are_the_only_terminal_states() -> None:
    terminal = {state for state, edges in TRANSITIONS.items() if not edges}

    assert terminal == {WorkerState.DONE, WorkerState.FAILED, WorkerState.KILLED}


@pytest.mark.parametrize("state", list(WorkerState))
def test_is_terminal_matches_transitions(state: WorkerState) -> None:
    assert is_terminal(state) == (not TRANSITIONS[state])


@pytest.mark.parametrize("from_state,to_state", _ALLOWED_PAIRS)
def test_every_allowed_edge_is_permitted(from_state: WorkerState, to_state: WorkerState) -> None:
    assert can_transition(from_state, to_state)
    assert_transition(from_state, to_state)  # Does not raise.


@pytest.mark.parametrize("from_state,to_state", _FORBIDDEN_PAIRS)
def test_every_forbidden_edge_is_rejected(from_state: WorkerState, to_state: WorkerState) -> None:
    assert not can_transition(from_state, to_state)
    with pytest.raises(InvalidWorkerTransitionError):
        assert_transition(from_state, to_state, worker_id="worker_test")


def test_worker_state_mirrors_wire_worker_state_member_for_member() -> None:
    assert {member.name for member in WorkerState} == {member.name for member in WireWorkerState}
    for member in WorkerState:
        assert member.value == WireWorkerState[member.name].value


@pytest.mark.parametrize("state", list(WorkerState))
def test_from_wire_and_to_wire_round_trip(state: WorkerState) -> None:
    wire = state.to_wire()

    assert WorkerState.from_wire(wire) is state
    assert wire.value == state.value
