"""Test the pollen error tree: one stable code per class, and the wire table of the contract.

The Pollen Packet (the thin gateway program on a device the Hive borrows) refuses a request with a
``control.error`` whose ``code`` is stable (gateway-contract section 15), so every class in the
tree sets its own code and no two share one; a code that has crossed the wire is never renamed.
The ten codes of the contract each have a class, the packet adds one more for a fault of its own,
and every code is a dotted, lowercase identifier that fits ``control.error``. The root is pollen's
own and not the Hive's, because pollen may import nothing from hivemind.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.errors.

Key invariants:
    - No two classes share a code.
    - Every code fits the shape ``control.error`` accepts.
    - No class derives from a hivemind error.

See Also:
    - docs/waggle/gateway-contract.md section 15.
"""

from __future__ import annotations

import inspect
import re

import pytest

import pollen.errors as errors
from pollen.errors import WIRE_CODES, PollenError
from waggle.messages.control import ErrorMessage
from waggle.messages.control.protocol import ERROR_CODE_PATTERN

CONTRACT_CODES = {
    "pollen.capability_denied",
    "pollen.path_refused",
    "pollen.not_found",
    "pollen.start_failed",
    "pollen.quota_exceeded",
    "pollen.transfer_refused",
    "pollen.lease_refused",
    "pollen.unsupported",
    "hive.session.lease_not_open",
    "waggle.codec.invalid_payload",
}


def _classes() -> list[type[PollenError]]:
    """Return every error class the module defines, the root included."""
    return [
        member
        for _, member in inspect.getmembers(errors, inspect.isclass)
        if issubclass(member, PollenError)
    ]


def test_no_two_classes_share_a_code() -> None:
    codes = [cls.code for cls in _classes()]
    assert len(codes) == len(set(codes))


def test_every_code_is_a_dotted_lowercase_identifier_a_control_error_accepts() -> None:
    for cls in _classes():
        assert re.fullmatch(ERROR_CODE_PATTERN, cls.code), cls
        ErrorMessage(code=cls.code, message="x", failed_kind=None, is_retryable=cls.is_retryable)


def test_the_contracts_ten_codes_are_on_the_wire_table_with_the_packets_own_fault() -> None:
    assert CONTRACT_CODES <= WIRE_CODES
    assert {errors.InternalError.code} == WIRE_CODES - CONTRACT_CODES


def test_no_refusal_the_packet_makes_is_retryable() -> None:
    assert not any(cls.is_retryable for cls in _classes())


def test_the_denied_enrolment_is_an_enrolment_error_with_the_hives_code() -> None:
    assert issubclass(errors.EnrolmentDeniedError, errors.EnrolmentError)
    assert errors.EnrolmentDeniedError.code == "hive.swarm.enrol_denied"


def test_the_tree_has_its_own_root() -> None:
    for cls in _classes():
        assert not any(base.__module__.startswith("hivemind") for base in cls.__mro__)


def test_a_message_is_the_sentence_it_was_raised_with() -> None:
    with pytest.raises(errors.NotFoundError, match="Nothing there"):
        raise errors.NotFoundError("Nothing there.")
