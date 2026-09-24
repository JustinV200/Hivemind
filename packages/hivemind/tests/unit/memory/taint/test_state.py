"""Tests for hivemind.memory.taint.state: every allowed taint edge, and every forbidden one raising.

Codingrules 14.3: "Every state machine has a test that walks every allowed transition and asserts
every forbidden one raises." Appendix C, "Taint label": unlabelled or CLEARED -> TAINTED -> CLEARED.

Fits into the Hive:
    Mirrors src/hivemind/memory/taint/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.taint.state for TRANSITIONS and assert_transition.
"""

from __future__ import annotations

import pytest

from hivemind.memory.errors import InvalidTaintTransitionError
from hivemind.memory.taint import TRANSITIONS, TaintState, assert_transition, can_transition

_STARTS: tuple[TaintState | None, ...] = (None, TaintState.TAINTED, TaintState.CLEARED)
_ALLOWED = {
    (None, TaintState.TAINTED),
    (TaintState.TAINTED, TaintState.CLEARED),
    (TaintState.CLEARED, TaintState.TAINTED),
}
_ALL = [(start, end) for start in _STARTS for end in TaintState]


def test_the_table_has_one_entry_per_starting_point_and_matches_appendix_c() -> None:
    edges = {(start, end) for start, ends in TRANSITIONS.items() for end in ends}

    assert set(TRANSITIONS) == set(_STARTS)
    assert edges == _ALLOWED


@pytest.mark.parametrize(("start", "end"), sorted(_ALLOWED, key=str), ids=str)
def test_every_allowed_edge_passes(start: TaintState | None, end: TaintState) -> None:
    assert can_transition(start, end)
    assert_transition(start, end, "event_x")


@pytest.mark.parametrize(("start", "end"), [edge for edge in _ALL if edge not in _ALLOWED], ids=str)
def test_every_forbidden_edge_raises(start: TaintState | None, end: TaintState) -> None:
    assert not can_transition(start, end)
    with pytest.raises(InvalidTaintTransitionError, match="cannot move"):
        assert_transition(start, end, "event_x")
