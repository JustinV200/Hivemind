"""Tests for hivemind.memory.errors: the memory error tree's messages and attributes.

Fits into the Hive:
    Mirrors src/hivemind/memory/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.errors for the module under test.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.common.errors import HiveMindError, NotFoundError, PermissionDeniedError
from hivemind.memory.errors import (
    ClearanceError,
    HandoffNotFoundError,
    MemoryTierError,
    NoteTooLongError,
)


def test_memory_tier_error_is_a_hivemind_error() -> None:
    assert issubclass(MemoryTierError, HiveMindError)


def test_clearance_error_carries_both_clearances_and_a_message() -> None:
    error = ClearanceError(HoneyClearance.C2, HoneyClearance.C1)

    assert issubclass(ClearanceError, PermissionDeniedError)
    assert error.item_clearance is HoneyClearance.C2
    assert error.allowance is HoneyClearance.C1
    assert "C2" in str(error)
    assert "C1" in str(error)


def test_handoff_not_found_error_carries_the_event_id() -> None:
    error = HandoffNotFoundError("event_missing")

    assert issubclass(HandoffNotFoundError, NotFoundError)
    assert error.event_id == "event_missing"
    assert "event_missing" in str(error)


def test_note_too_long_error_carries_length_and_limit() -> None:
    error = NoteTooLongError(1_500, 1_000)

    assert issubclass(NoteTooLongError, MemoryTierError)
    assert error.length == 1_500
    assert error.limit == 1_000
    assert "1500" in str(error)
    assert "1000" in str(error)
