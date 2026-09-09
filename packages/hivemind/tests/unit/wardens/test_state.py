"""Tests for hivemind.wardens.state: the WardenState machine and its wire mirror.

Fits into the Hive:
    Mirrors src/hivemind/wardens/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.state for the module under test.
    - .claude/codingrules.md Appendix C, "Warden" row, for the transition table asserted here.
"""

from __future__ import annotations

import pytest

from hivemind.wardens.errors import InvalidWardenTransitionError
from hivemind.wardens.state import (
    TRANSITIONS,
    WardenState,
    assert_transition,
    can_transition,
    is_terminal,
)
from waggle.messages.supervision import WardenState as WireWardenState

# Every allowed edge, read literally off this module's own table (module docstring's own
# expansion of Appendix C's compressed notation).
_ALLOWED_EDGES: tuple[tuple[WardenState, WardenState], ...] = (
    (WardenState.STARTING, WardenState.ACTIVE),
    (WardenState.STARTING, WardenState.WATCH),
    (WardenState.ACTIVE, WardenState.WATCH),
    (WardenState.ACTIVE, WardenState.OFFLINE),
    (WardenState.ACTIVE, WardenState.CLUSTERED),
    (WardenState.ACTIVE, WardenState.MIGRATING),
    (WardenState.ACTIVE, WardenState.STOPPED),
    (WardenState.WATCH, WardenState.ACTIVE),
    (WardenState.WATCH, WardenState.OFFLINE),
    (WardenState.WATCH, WardenState.CLUSTERED),
    (WardenState.WATCH, WardenState.STOPPED),
    (WardenState.OFFLINE, WardenState.ACTIVE),
    (WardenState.OFFLINE, WardenState.CLUSTERED),
    (WardenState.OFFLINE, WardenState.STOPPED),
    (WardenState.CLUSTERED, WardenState.ACTIVE),
    (WardenState.CLUSTERED, WardenState.STOPPED),
    (WardenState.MIGRATING, WardenState.ACTIVE),
    (WardenState.MIGRATING, WardenState.STOPPED),
)


def test_warden_state_mirrors_the_wire_form_member_for_member() -> None:
    assert {member.name for member in WardenState} == {member.name for member in WireWardenState}
    for member in WardenState:
        assert member.value == WireWardenState[member.name].value


@pytest.mark.parametrize("member", list(WardenState))
def test_from_wire_and_to_wire_round_trip(member: WardenState) -> None:
    wire = member.to_wire()
    assert WardenState.from_wire(wire) is member


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_every_allowed_edge_transitions_without_raising(
    from_state: WardenState, to_state: WardenState
) -> None:
    assert can_transition(from_state, to_state)
    assert_transition(from_state, to_state)  # never raises


def test_transitions_table_has_exactly_the_allowed_edges() -> None:
    actual = {(frm, to) for frm, tos in TRANSITIONS.items() for to in tos}
    assert actual == set(_ALLOWED_EDGES)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (WardenState.STARTING, WardenState.STOPPED),  # must lease (or refuse) first
        (WardenState.STARTING, WardenState.OFFLINE),
        (WardenState.STOPPED, WardenState.ACTIVE),  # terminal
        (WardenState.STOPPED, WardenState.STARTING),
        (WardenState.ACTIVE, WardenState.STARTING),  # never returns to STARTING
        (WardenState.WATCH, WardenState.MIGRATING),  # only an ACTIVE Warden migrates
    ],
)
def test_forbidden_edges_raise(from_state: WardenState, to_state: WardenState) -> None:
    assert not can_transition(from_state, to_state)
    with pytest.raises(InvalidWardenTransitionError):
        assert_transition(from_state, to_state, warden_id="warden_test")


def test_is_terminal_is_true_only_for_stopped() -> None:
    for member in WardenState:
        assert is_terminal(member) == (member is WardenState.STOPPED)


def test_invalid_transition_error_names_both_states_and_the_warden_id() -> None:
    with pytest.raises(InvalidWardenTransitionError) as excinfo:
        assert_transition(WardenState.STOPPED, WardenState.ACTIVE, warden_id="warden_abc")
    message = str(excinfo.value)
    assert "STOPPED" in message
    assert "ACTIVE" in message
    assert "warden_abc" in message
