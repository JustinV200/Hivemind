"""Tests for hivemind.basket.transfer.state: every allowed edge moves, every other one raises.

Fits into the Hive:
    Mirrors src/hivemind/basket/transfer/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.transfer.state for the module under test.
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.basket import TransferError
from hivemind.basket.transfer import TRANSITIONS, TransferPhase, advance

_PAIRS = list(itertools.product(TransferPhase, TransferPhase))
_ALLOWED = [(a, b) for a, b in _PAIRS if b in TRANSITIONS[a]]
_FORBIDDEN = [(a, b) for a, b in _PAIRS if b not in TRANSITIONS[a]]


@pytest.mark.parametrize(("current", "target"), _ALLOWED)
def test_an_allowed_edge_moves_to_the_target(current: TransferPhase, target: TransferPhase) -> None:
    assert advance(current, target, "a" * 64) is target


@pytest.mark.parametrize(("current", "target"), _FORBIDDEN)
def test_every_other_edge_raises(current: TransferPhase, target: TransferPhase) -> None:
    with pytest.raises(TransferError):
        advance(current, target, "a" * 64)


def test_complete_and_refused_are_terminal_and_say_so() -> None:
    for terminal in (TransferPhase.COMPLETE, TransferPhase.REFUSED):
        assert TRANSITIONS[terminal] == frozenset()
        with pytest.raises(TransferError) as raised:
            advance(terminal, TransferPhase.OFFERED, "a" * 64)
        assert raised.value.reason_code == "complete"


def test_a_non_terminal_forbidden_edge_is_unknown() -> None:
    with pytest.raises(TransferError) as raised:
        advance(TransferPhase.IDLE, TransferPhase.COMPLETE, "a" * 64)

    assert raised.value.reason_code == "unknown"


def test_the_table_has_the_designs_shape() -> None:
    assert TRANSITIONS[TransferPhase.IDLE] == {TransferPhase.OFFERED, TransferPhase.REFUSED}
    assert TransferPhase.COMPLETE in TRANSITIONS[TransferPhase.TRANSFERRING]
