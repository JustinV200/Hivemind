"""Tests for hivemind.guard.errors: GuardError, InvalidCapabilityError, CapabilityWideningError.

Fits into the Hive:
    Mirrors src/hivemind/guard/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.errors for the module under test.
"""

from __future__ import annotations

from hivemind.common.errors import HiveMindError, PermissionDeniedError
from hivemind.guard.errors import CapabilityWideningError, GuardError, InvalidCapabilityError


def test_guard_error_is_a_hivemind_error() -> None:
    assert issubclass(GuardError, HiveMindError)


def test_invalid_capability_error_is_a_guard_error() -> None:
    # No base category in hivemind.common.errors fits a malformed capability string (see the
    # module docstring), so this subclasses GuardError directly.
    assert issubclass(InvalidCapabilityError, GuardError)


def test_invalid_capability_error_carries_the_offending_spec() -> None:
    error = InvalidCapabilityError("bogus:thing")

    assert error.spec == "bogus:thing"
    assert "bogus:thing" in str(error)


def test_capability_widening_error_is_a_permission_denied_error() -> None:
    # Asking `attenuate` to widen maps cleanly onto "the caller lacks the capability an operation
    # requires" (see the module docstring), so this subclasses PermissionDeniedError directly
    # rather than GuardError.
    assert issubclass(CapabilityWideningError, PermissionDeniedError)


def test_capability_widening_error_carries_the_offending_capability_string() -> None:
    error = CapabilityWideningError("fs:write:/etc/passwd")

    assert error.offending == "fs:write:/etc/passwd"
    assert "fs:write:/etc/passwd" in str(error)


def test_guard_errors_each_have_their_own_code() -> None:
    codes = {
        GuardError.code,
        InvalidCapabilityError.code,
        CapabilityWideningError.code,
    }

    assert len(codes) == 3
