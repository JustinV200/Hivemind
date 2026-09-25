"""Tests for hivemind.entrance.auth.confirm.state: a pending confirmation is settled once.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/confirm/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.confirm.state for the module under test.
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.entrance.auth.confirm import (
    TRANSITIONS,
    PendingStatus,
    assert_pending_transition,
    can_settle,
)
from hivemind.entrance.errors import InvalidPendingTransitionError

_ALLOWED = [(status, target) for status, targets in TRANSITIONS.items() for target in targets]
_FORBIDDEN = [
    pair for pair in itertools.product(PendingStatus, PendingStatus) if pair not in _ALLOWED
]


def test_the_table_has_one_entry_per_status_and_settles_only_from_pending() -> None:
    assert set(TRANSITIONS) == set(PendingStatus)
    assert {status for status, _ in _ALLOWED} == {PendingStatus.PENDING}
    assert {target for _, target in _ALLOWED} == {
        PendingStatus.CONFIRMED,
        PendingStatus.EXPIRED,
        PendingStatus.CANCELLED,
    }


@pytest.mark.parametrize(("status", "target"), _ALLOWED)
def test_every_allowed_edge_is_allowed(status: PendingStatus, target: PendingStatus) -> None:
    assert can_settle(status, target)
    assert_pending_transition(status, target)


@pytest.mark.parametrize(("status", "target"), _FORBIDDEN)
def test_every_forbidden_edge_raises(status: PendingStatus, target: PendingStatus) -> None:
    assert not can_settle(status, target)
    with pytest.raises(InvalidPendingTransitionError):
        assert_pending_transition(status, target)
