"""Tests for hivemind.forage.errors: ForageError and its subclasses.

Fits into the Hive:
    Mirrors src/hivemind/forage/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.errors for the module under test.
"""

from __future__ import annotations

from hivemind.common.errors import ConflictError, HiveMindError, NotFoundError
from hivemind.forage.errors import (
    AllocationError,
    ForageError,
    InvalidGrantTransitionError,
    UnknownSourceError,
)
from hivemind.forage.grant_state import GrantState


def test_forage_error_is_a_hivemind_error() -> None:
    assert issubclass(ForageError, HiveMindError)


def test_unknown_source_error_is_a_not_found_error_and_carries_the_id() -> None:
    error = UnknownSourceError("test-source")

    assert isinstance(error, NotFoundError)
    assert error.source_id == "test-source"
    assert "test-source" in str(error)


def test_allocation_error_is_a_forage_error() -> None:
    assert issubclass(AllocationError, ForageError)


def test_invalid_grant_transition_error_is_a_conflict_error_and_carries_the_states() -> None:
    error = InvalidGrantTransitionError(GrantState.REVOKED, GrantState.ACTIVE, subject_id="grant_1")

    assert isinstance(error, ConflictError)
    assert error.from_state is GrantState.REVOKED
    assert error.to_state is GrantState.ACTIVE
    assert error.subject_id == "grant_1"
    assert "REVOKED" in str(error)
    assert "ACTIVE" in str(error)
    assert "grant_1" in str(error)


def test_invalid_grant_transition_error_message_omits_subject_when_absent() -> None:
    error = InvalidGrantTransitionError(GrantState.ISSUED, GrantState.EXHAUSTED)

    assert error.subject_id is None
    assert "None" not in str(error)


def test_every_forage_error_class_has_its_own_code() -> None:
    classes = (ForageError, UnknownSourceError, AllocationError, InvalidGrantTransitionError)
    codes = [cls.code for cls in classes]

    assert len(set(codes)) == len(codes)
