"""Unit tests for hivemind.hive.cell_state: every edge in TRANSITIONS, and the DORMANT invariant.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/cell_state.py
    (codingrules section 3). Walks every allowed transition and asserts every forbidden one
    raises (codingrules section 14.3: "every state machine has a test that walks every allowed
    transition and asserts every forbidden one raises").

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.cell_state for VirtualCellStatus, TRANSITIONS, can_transition,
      assert_transition, can_enter_dormant and assert_dormant_allowed, all under test.
"""

from __future__ import annotations

import pytest

from hivemind.cell import CombShieldLevel
from hivemind.hive.cell_state import (
    TRANSITIONS,
    VirtualCellStatus,
    assert_dormant_allowed,
    assert_transition,
    can_enter_dormant,
    can_transition,
)
from hivemind.hive.errors import InvalidCellTransitionError

# Explicit tuple/frozenset annotations, rather than leaning on itertools.product's inference:
# mypy --strict otherwise widens the pair element type through the generator expressions below,
# which then makes sorted()'s own lambda key untypeable (pair[0] on an "object").
_StatusPair = tuple[VirtualCellStatus, VirtualCellStatus]
_ALL_STATUSES = tuple(VirtualCellStatus)
_ALL_PAIRS: tuple[_StatusPair, ...] = tuple(
    (from_status, to_status) for from_status in _ALL_STATUSES for to_status in _ALL_STATUSES
)
_ALLOWED_PAIRS: frozenset[_StatusPair] = frozenset(
    (from_status, to_status) for from_status, edges in TRANSITIONS.items() for to_status in edges
)
_FORBIDDEN_PAIRS: tuple[_StatusPair, ...] = tuple(
    pair for pair in _ALL_PAIRS if pair not in _ALLOWED_PAIRS
)


def _sort_key(pair: _StatusPair) -> tuple[str, str]:
    """Sort a status pair by its two member names, for stable, readable parametrize ids."""
    return (pair[0].value, pair[1].value)


_SORTED_ALLOWED_PAIRS = sorted(_ALLOWED_PAIRS, key=_sort_key)
_SORTED_FORBIDDEN_PAIRS = sorted(_FORBIDDEN_PAIRS, key=_sort_key)


def test_transitions_has_exactly_one_entry_per_status() -> None:
    assert set(TRANSITIONS) == set(VirtualCellStatus)


def test_destroyed_and_failed_are_the_only_terminal_states() -> None:
    terminal = {status for status, edges in TRANSITIONS.items() if not edges}

    assert terminal == {VirtualCellStatus.DESTROYED, VirtualCellStatus.FAILED}


@pytest.mark.parametrize("from_status,to_status", _SORTED_ALLOWED_PAIRS)
def test_every_allowed_edge_transitions_cleanly(
    from_status: VirtualCellStatus, to_status: VirtualCellStatus
) -> None:
    assert can_transition(from_status, to_status) is True
    assert_transition(from_status, to_status, cell_id="cell_test")  # must not raise


@pytest.mark.parametrize("from_status,to_status", _SORTED_FORBIDDEN_PAIRS)
def test_every_forbidden_edge_raises(
    from_status: VirtualCellStatus, to_status: VirtualCellStatus
) -> None:
    assert can_transition(from_status, to_status) is False
    with pytest.raises(InvalidCellTransitionError) as exc_info:
        assert_transition(from_status, to_status, cell_id="cell_test")
    assert exc_info.value.from_status is from_status
    assert exc_info.value.to_status is to_status
    assert exc_info.value.cell_id == "cell_test"


@pytest.mark.parametrize("comb_shield", [CombShieldLevel.MEADOW, CombShieldLevel.PROPOLIS])
def test_meadow_and_propolis_may_enter_dormant(comb_shield: CombShieldLevel) -> None:
    assert can_enter_dormant(comb_shield) is True
    assert_dormant_allowed(comb_shield, cell_id="cell_test")  # must not raise


def test_night_veil_may_never_enter_dormant() -> None:
    assert can_enter_dormant(CombShieldLevel.NIGHT_VEIL) is False
    with pytest.raises(InvalidCellTransitionError) as exc_info:
        assert_dormant_allowed(CombShieldLevel.NIGHT_VEIL, cell_id="cell_test")
    assert exc_info.value.from_status is VirtualCellStatus.RELEASED
    assert exc_info.value.to_status is VirtualCellStatus.DORMANT
    assert "Night Veil" in str(exc_info.value)
