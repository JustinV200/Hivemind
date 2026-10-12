"""Tests for hivemind.royal_jelly.tool_state: every edge allowed, every other edge refused.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/tool_state.py (codingrules section 3 and 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.tool_state for the module under test.
    - .claude/codingrules.md Appendix C for the Tool row this table implements.
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.royal_jelly.errors import ToolTransitionError
from hivemind.royal_jelly.tool_state import (
    TERMINAL_STATES,
    TRANSITIONS,
    ToolState,
    can_transition,
    transition,
)

# Appendix C's Tool row, written out independently of the table under test.
_ALLOWED = {
    (ToolState.REQUESTED, ToolState.SCAFFOLDED),
    (ToolState.SCAFFOLDED, ToolState.QUARANTINING),
    (ToolState.QUARANTINING, ToolState.PROMOTED),
    (ToolState.QUARANTINING, ToolState.REJECTED),
    (ToolState.PROMOTED, ToolState.RETIRED),
}
_ALL_PAIRS = list(itertools.product(ToolState, ToolState))


def test_table_has_one_entry_per_state() -> None:
    assert set(TRANSITIONS) == set(ToolState)


def test_table_lists_exactly_appendix_c_edges() -> None:
    listed = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}

    assert listed == _ALLOWED


def test_terminal_states_are_exactly_those_with_no_edge_out() -> None:
    assert {state for state, targets in TRANSITIONS.items() if not targets} == TERMINAL_STATES


@pytest.mark.parametrize(("current", "target"), sorted(_ALLOWED, key=str))
def test_transition_allows_every_listed_edge(current: ToolState, target: ToolState) -> None:
    assert can_transition(current, target)
    assert transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"), [pair for pair in _ALL_PAIRS if pair not in _ALLOWED]
)
def test_transition_refuses_every_unlisted_edge(current: ToolState, target: ToolState) -> None:
    assert not can_transition(current, target)
    with pytest.raises(ToolTransitionError):
        transition(current, target)
