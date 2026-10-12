"""Tests for hivemind.basket.errors: the closed reason vocabularies and the carried events.

Fits into the Hive:
    Mirrors src/hivemind/basket/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.errors for the module under test.
"""

from __future__ import annotations

from typing import get_args

from hivemind.basket.errors import (
    REFUSAL_REASONS,
    TRANSFER_FAILURES,
    BasketError,
    BlobIntegrityError,
    BlobNotFoundError,
    BlobRefusedError,
    BlobTooLargeError,
    RefusalReason,
    ShipmentNotFoundError,
    TransferError,
    TransferFailure,
)
from hivemind.common.errors import HiveMindError, NotFoundError


def test_the_runtime_reason_sets_match_their_literal_types() -> None:
    assert frozenset(get_args(RefusalReason.__value__)) == REFUSAL_REASONS
    assert frozenset(get_args(TransferFailure.__value__)) == TRANSFER_FAILURES


def test_the_design_names_every_refusal_reason() -> None:
    assert {
        "scanner",
        "clearance",
        "tainted",
        "no_room",
        "unknown_signer",
        "integrity",
        "platform",
        "retired",
    } == REFUSAL_REASONS
    assert {"offset", "complete", "unknown", "too_large"} == TRANSFER_FAILURES


def test_every_error_is_a_basket_error_and_a_hivemind_error_with_its_own_code() -> None:
    errors: list[BasketError] = [
        BlobNotFoundError("a" * 64),
        ShipmentNotFoundError(3),
        BlobTooLargeError(10, 5),
        BlobRefusedError("scanner", "flagged"),
        BlobIntegrityError("a" * 64, "bad"),
        TransferError("offset", "gap"),
    ]

    assert all(isinstance(error, HiveMindError) for error in errors)
    assert len({error.code for error in errors}) == len(errors)
    assert all(error.code.startswith("hivemind.basket.") for error in errors)


def test_not_found_errors_are_also_not_found_errors() -> None:
    assert isinstance(BlobNotFoundError("x"), NotFoundError)
    assert isinstance(ShipmentNotFoundError(1), NotFoundError)


def test_a_refusal_carries_its_reason_code_and_a_sentence() -> None:
    error = BlobRefusedError("retired", "blob x was withdrawn.")

    assert error.reason_code == "retired" and "retired" in str(error)
    assert error.events == ()
