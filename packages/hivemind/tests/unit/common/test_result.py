"""Tests for hivemind.common.result: Ok, Err and their unwrap behaviour.

Fits into the Hive:
    Mirrors src/hivemind/common/result.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.result for the module under test.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hivemind.common.result import Err, Ok, Result, ResultUnwrapError


def test_ok_reports_is_ok_and_unwraps_its_value() -> None:
    result: Result[int, str] = Ok(3)

    assert result.is_ok() is True
    assert result.unwrap() == 3


def test_ok_unwrap_err_raises() -> None:
    result: Result[int, str] = Ok(3)

    with pytest.raises(ResultUnwrapError, match="unwrap_err"):
        result.unwrap_err()


def test_err_reports_is_ok_false_and_unwraps_its_error() -> None:
    result: Result[int, str] = Err("boom")

    assert result.is_ok() is False
    assert result.unwrap_err() == "boom"


def test_err_unwrap_raises() -> None:
    result: Result[int, str] = Err("boom")

    with pytest.raises(ResultUnwrapError, match="unwrap\\(\\)"):
        result.unwrap()


def test_ok_is_frozen() -> None:
    ok = Ok(1)

    with pytest.raises(FrozenInstanceError):
        ok.value = 2  # type: ignore[misc]  # deliberately mutating a frozen dataclass to test it


def test_err_is_frozen() -> None:
    err = Err("boom")

    with pytest.raises(FrozenInstanceError):
        err.error = "other"  # type: ignore[misc]  # deliberately mutating a frozen dataclass
