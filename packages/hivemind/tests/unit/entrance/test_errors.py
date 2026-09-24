"""Tests for hivemind.entrance.errors: the Entrance's error tree and its stable codes.

Fits into the Hive:
    Mirrors src/hivemind/entrance/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.errors for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import (
    ConflictError,
    HiveMindError,
    NotFoundError,
    PermissionDeniedError,
)
from hivemind.entrance import errors
from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.errors import (
    CapabilityCeilingError,
    ChallengeRejectedError,
    ConsoleProtectedError,
    DeviceStatusConflictError,
    EnrolmentRefusedError,
    EntranceError,
    InvalidDeviceEntryError,
    InvalidDeviceTransitionError,
    InviteAlreadyUsedError,
    KeyUnwrapError,
    StewardGrantError,
)

# Every class the module exports, read from its own __all__ so a new one is covered automatically.
_ERROR_CLASSES = [getattr(errors, name) for name in errors.__all__]
_CODE = "c" * 64  # A code hash; long enough to show the message truncates it.


def test_every_entrance_error_descends_from_entrance_error_and_hivemind_error() -> None:
    for error_cls in _ERROR_CLASSES:
        assert issubclass(error_cls, EntranceError)
        assert issubclass(error_cls, HiveMindError)


def test_every_entrance_error_has_its_own_stable_code() -> None:
    codes = [error_cls.code for error_cls in _ERROR_CLASSES]

    assert len(codes) == len(set(codes))
    assert all(code.startswith("hivemind.entrance.") for code in codes)


@pytest.mark.parametrize(
    ("error_cls", "category"),
    [
        (errors.DeviceNotFoundError, NotFoundError),
        (errors.InviteNotFoundError, NotFoundError),
        (errors.OperatorNotInitialisedError, NotFoundError),
        (errors.DeviceAlreadyExistsError, ConflictError),
        (errors.InvalidDeviceTransitionError, ConflictError),
        (errors.InvalidDeviceEntryError, ConflictError),
        (errors.DeviceStatusConflictError, ConflictError),
        (errors.InviteAlreadyExistsError, ConflictError),
        (errors.InviteAlreadyUsedError, ConflictError),
        (errors.InviteExpiredError, ConflictError),
        (errors.OperatorAlreadyInitialisedError, ConflictError),
        (errors.PasskeyRejectedError, PermissionDeniedError),
        (errors.KeyUnwrapError, PermissionDeniedError),
        (errors.OperatorPasswordMismatchError, PermissionDeniedError),
        (errors.ChallengeRejectedError, PermissionDeniedError),
        (errors.EnrolmentRefusedError, PermissionDeniedError),
        (errors.CapabilityCeilingError, PermissionDeniedError),
        (errors.StewardGrantError, PermissionDeniedError),
        (errors.ConsoleProtectedError, PermissionDeniedError),
        (errors.InvalidApprovalError, ConflictError),
    ],
)
def test_each_refusal_also_belongs_to_its_common_category(
    error_cls: type[EntranceError], category: type[HiveMindError]
) -> None:
    assert issubclass(error_cls, category)


def test_transition_error_names_the_device_and_both_statuses() -> None:
    error = InvalidDeviceTransitionError(
        DeviceStatus.REVOKED, DeviceStatus.APPROVED, "device_01M221E4C10R4XDPNQNRX85AAA"
    )

    assert "device_01M221E4C10R4XDPNQNRX85AAA" in str(error)
    assert "REVOKED" in str(error)
    assert "APPROVED" in str(error)
    assert error.from_status is DeviceStatus.REVOKED


def test_transition_error_without_a_device_id_is_still_a_sentence() -> None:
    error = InvalidDeviceTransitionError(DeviceStatus.PENDING, DeviceStatus.LOCKED)

    assert str(error).startswith("Cannot move a device from PENDING to LOCKED")
    assert error.device_id is None


def test_entry_and_conflict_errors_name_the_statuses_involved() -> None:
    entry = InvalidDeviceEntryError("device_x", DeviceStatus.PENDING)
    conflict = DeviceStatusConflictError("device_x", DeviceStatus.PENDING, DeviceStatus.APPROVED)

    assert "PENDING" in str(entry)
    assert "is APPROVED, not PENDING" in str(conflict)
    assert conflict.actual is DeviceStatus.APPROVED


def test_invite_errors_show_only_a_prefix_of_the_code_hash() -> None:
    error = InviteAlreadyUsedError(_CODE)

    assert _CODE not in str(error)
    assert _CODE[:12] in str(error)
    assert error.code_hash == _CODE


def test_key_unwrap_error_names_the_secret_only() -> None:
    error = KeyUnwrapError("console.ed25519")

    assert str(error) == "The wrapped key 'console.ed25519' could not be opened with this password."


def test_the_refusals_a_device_sees_never_say_which_check_failed() -> None:
    assert str(EnrolmentRefusedError()) == str(EnrolmentRefusedError())
    assert "invite could not be redeemed" in str(EnrolmentRefusedError())
    assert str(ChallengeRejectedError()) == str(ChallengeRejectedError())


def test_approval_refusals_name_the_offending_capability() -> None:
    ceiling = CapabilityCeilingError("supersede")
    steward = StewardGrantError("device_x", "entrance:steward", "stewardship is loopback-only")
    general = StewardGrantError("device_x", None, "it is not a steward")

    assert (ceiling.capability, steward.capability, general.capability) == (
        "supersede",
        "entrance:steward",
        None,
    )
    assert "'supersede'" in str(ceiling)
    assert "may not grant 'entrance:steward'" in str(steward)
    assert "may not approve devices" in str(general)


def test_the_console_protection_says_how_to_replace_it() -> None:
    error = ConsoleProtectedError("device_x", "revoked")

    assert "cannot be revoked" in str(error)
    assert "operator password --reset" in str(error)
    assert error.device_id == "device_x"
