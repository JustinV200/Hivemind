"""Tests for hivemind.pheromone.errors: PheromoneError and its two subclasses.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.errors for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import HiveMindError
from hivemind.pheromone.errors import DuplicateEventError, PheromoneError, UnknownEventFamilyError

_SUBCLASSES = [
    (DuplicateEventError, "hivemind.pheromone.duplicate_event"),
    (UnknownEventFamilyError, "hivemind.pheromone.unknown_family"),
]


def test_pheromone_error_has_its_own_root_code() -> None:
    error = PheromoneError("a full sentence message.")

    assert isinstance(error, HiveMindError)
    assert error.code == "hivemind.pheromone.error"
    assert str(error) == "a full sentence message."


@pytest.mark.parametrize(("error_cls", "expected_code"), _SUBCLASSES)
def test_subclass_is_a_pheromone_error_with_its_own_code(
    error_cls: type[PheromoneError], expected_code: str
) -> None:
    error = error_cls("a full sentence message.")

    assert isinstance(error, PheromoneError)
    assert isinstance(error, HiveMindError)
    assert error.code == expected_code
    assert str(error) == "a full sentence message."


def test_pheromone_errors_each_have_a_distinct_code() -> None:
    codes = [PheromoneError.code, *(cls.code for cls, _ in _SUBCLASSES)]

    assert len(codes) == len(set(codes))
