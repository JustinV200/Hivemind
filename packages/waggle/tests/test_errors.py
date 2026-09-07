"""Tests for waggle.errors: WaggleError's root and InvalidIdError's place in it.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.errors in isolation.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.errors for the module under test.
"""

from __future__ import annotations

from waggle.errors import InvalidIdError, WaggleError


def test_invalid_id_error_is_a_waggle_error() -> None:
    error = InvalidIdError("bad id.")

    assert isinstance(error, WaggleError)


def test_waggle_error_inherits_only_from_exception() -> None:
    # codingrules section 10: waggle has its own root because it cannot import hivemind, so
    # WaggleError must never end up inheriting from HiveMindError, directly or indirectly.
    assert WaggleError.__bases__ == (Exception,)


def test_waggle_error_carries_its_message() -> None:
    error = WaggleError("something went wrong.")

    assert str(error) == "something went wrong."
