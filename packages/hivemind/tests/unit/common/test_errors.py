"""Tests for hivemind.common.errors: HiveMindError's root and its six base categories.

Fits into the Hive:
    Mirrors src/hivemind/common/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.errors for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import (
    ConfigurationError,
    ConflictError,
    DeadlineExceededError,
    HiveMindError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
)

_CATEGORIES = [
    (ConfigurationError, "hivemind.configuration_error"),
    (NotFoundError, "hivemind.not_found"),
    (ConflictError, "hivemind.conflict"),
    (PermissionDeniedError, "hivemind.permission_denied"),
    (DeadlineExceededError, "hivemind.deadline_exceeded"),
    (InvariantViolationError, "hivemind.invariant_violation"),
]


def test_hivemind_error_has_the_default_root_code() -> None:
    error = HiveMindError("root error.")

    assert error.code == "hivemind.error"
    assert str(error) == "root error."


@pytest.mark.parametrize(("error_cls", "expected_code"), _CATEGORIES)
def test_base_category_is_a_hivemind_error_with_its_own_code(
    error_cls: type[HiveMindError], expected_code: str
) -> None:
    error = error_cls("a full sentence message.")

    assert isinstance(error, HiveMindError)
    assert error.code == expected_code
    assert str(error) == "a full sentence message."


def test_base_categories_each_have_a_distinct_code() -> None:
    codes = [code for _, code in _CATEGORIES]

    assert len(codes) == len(set(codes))
