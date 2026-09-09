"""Tests for hivemind.supervision.errors.

Fits into the Hive:
    Mirrors src/hivemind/supervision/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.errors for the module under test.
"""

from __future__ import annotations

from hivemind.common.errors import ConflictError, HiveMindError, NotFoundError
from hivemind.supervision.alarm import AlarmState
from hivemind.supervision.errors import (
    InvalidAlarmTransitionError,
    PolicyError,
    SupervisionError,
    UnknownChildError,
)


def test_supervision_error_descends_from_hivemind_error() -> None:
    assert issubclass(SupervisionError, HiveMindError)


def test_invalid_alarm_transition_error_descends_from_conflict_error() -> None:
    assert issubclass(InvalidAlarmTransitionError, ConflictError)


def test_policy_error_descends_from_supervision_error() -> None:
    assert issubclass(PolicyError, SupervisionError)


def test_unknown_child_error_descends_from_not_found_error() -> None:
    assert issubclass(UnknownChildError, NotFoundError)


def test_every_error_has_its_own_code() -> None:
    codes = {
        SupervisionError.code,
        InvalidAlarmTransitionError.code,
        PolicyError.code,
        UnknownChildError.code,
    }

    assert len(codes) == 4


def test_unknown_child_error_message_names_the_child() -> None:
    error = UnknownChildError("worker_abc")

    assert error.child == "worker_abc"
    assert "worker_abc" in str(error)


def test_invalid_alarm_transition_error_message_names_the_states() -> None:
    error = InvalidAlarmTransitionError(AlarmState.RESOLVED, AlarmState.HANDLING)

    assert "RESOLVED" in str(error)
    assert "HANDLING" in str(error)
