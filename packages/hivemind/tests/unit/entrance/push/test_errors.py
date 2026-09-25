"""Tests for hivemind.entrance.push.errors: the push channel's error tree and stable codes.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.errors for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import ConflictError, HiveMindError, PermissionDeniedError
from hivemind.entrance import errors as entrance_errors
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.push import errors
from hivemind.entrance.push.errors import (
    DestinationRefusedError,
    PushConfigError,
    PushError,
    PushRegistrationRefusedError,
    SubscriptionExistsError,
)

# Every class the module exports, read from its own __all__ so a new one is covered automatically.
_ERROR_CLASSES = [getattr(errors, name) for name in errors.__all__]


def test_every_push_error_descends_from_push_error_and_entrance_error() -> None:
    for error_cls in _ERROR_CLASSES:
        assert issubclass(error_cls, PushError)
        assert issubclass(error_cls, EntranceError)
        assert issubclass(error_cls, HiveMindError)


def test_every_push_error_has_its_own_code_unused_by_the_rest_of_the_entrance() -> None:
    codes = [error_cls.code for error_cls in _ERROR_CLASSES]
    entrance_codes = {getattr(entrance_errors, name).code for name in entrance_errors.__all__}

    assert len(codes) == len(set(codes))
    assert all(code.startswith("hivemind.entrance.") for code in codes)
    assert not set(codes) & entrance_codes


@pytest.mark.parametrize(
    ("error_cls", "category"),
    [
        (PushRegistrationRefusedError, PermissionDeniedError),
        (DestinationRefusedError, PermissionDeniedError),
        (SubscriptionExistsError, ConflictError),
    ],
)
def test_each_refusal_also_belongs_to_its_common_category(
    error_cls: type[PushError], category: type[HiveMindError]
) -> None:
    assert issubclass(error_cls, category)


def test_registration_refusal_names_the_device_and_the_reason() -> None:
    error = PushRegistrationRefusedError("device_01J8ZQ7X9K3M2N4P5Q6R7S8T9V", "it is LOCKED")

    assert "device_01J8ZQ7X9K3M2N4P5Q6R7S8T9V" in str(error)
    assert error.reason == "it is LOCKED"


def test_config_error_names_the_source_never_the_value() -> None:
    error = PushConfigError("HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY", "base64url of a scalar")

    assert "HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY" in str(error)
    assert error.source == "HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY"
