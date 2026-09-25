"""Tests for hivemind.entrance.expose.errors: the exposure layer's refusals and their codes.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.errors for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import ConfigurationError, HiveMindError, PermissionDeniedError
from hivemind.entrance import errors as entrance_errors
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.expose import ExposureRule
from hivemind.entrance.expose import errors as expose_errors
from hivemind.entrance.expose.errors import (
    CertificateAuthorityError,
    CertificateIssueError,
    CertificateRequestError,
    ExposeError,
    ExposureRefusedError,
)
from hivemind.manifest.schema import EntranceExposure

# Every class the module exports, read from its own __all__ so a new one is covered automatically.
_ERROR_CLASSES = [getattr(expose_errors, name) for name in expose_errors.__all__]


def test_every_expose_error_descends_from_the_entrance_root() -> None:
    for error_cls in _ERROR_CLASSES:
        assert issubclass(error_cls, ExposeError)
        assert issubclass(error_cls, EntranceError)
        assert issubclass(error_cls, HiveMindError)


def test_every_expose_error_has_its_own_code_distinct_from_the_entrances() -> None:
    codes = [error_cls.code for error_cls in _ERROR_CLASSES]
    entrance_codes = {getattr(entrance_errors, name).code for name in entrance_errors.__all__}

    assert len(codes) == len(set(codes))
    assert all(code.startswith("hivemind.entrance.") for code in codes)
    assert not set(codes) & entrance_codes


@pytest.mark.parametrize(
    ("error_cls", "category"),
    [
        (ExposureRefusedError, ConfigurationError),
        (CertificateRequestError, PermissionDeniedError),
        (CertificateIssueError, ConfigurationError),
    ],
)
def test_each_refusal_sits_in_its_generic_category(
    error_cls: type[HiveMindError], category: type[HiveMindError]
) -> None:
    assert issubclass(error_cls, category)


def test_an_exposure_refusal_names_the_mode_the_rule_and_the_detail() -> None:
    error = ExposureRefusedError(
        EntranceExposure.LAN, ExposureRule.MUTUAL_TLS_REQUIRED, "mutual_tls is false."
    )

    assert error.mode is EntranceExposure.LAN
    assert error.rule is ExposureRule.MUTUAL_TLS_REQUIRED
    assert "'lan'" in str(error)
    assert "mutual_tls_required" in str(error)
    assert ExposureRule.MUTUAL_TLS_REQUIRED.requirement in str(error)
    assert str(error).endswith("mutual_tls is false.")


def test_a_request_refusal_names_the_device_and_the_reason() -> None:
    error = CertificateRequestError("device_x", "its signature does not verify")

    assert error.device_id == "device_x"
    assert str(error) == (
        "The certificate request for device device_x was refused: its signature does not verify."
    )


def test_the_authority_error_is_a_plain_expose_error() -> None:
    assert issubclass(CertificateAuthorityError, ExposeError)
    assert CertificateAuthorityError.code == "hivemind.entrance.certificate_authority_invalid"
