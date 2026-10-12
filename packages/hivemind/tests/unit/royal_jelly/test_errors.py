"""Tests for hivemind.royal_jelly.errors: one root, stable codes, every refusal catchable alike.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.errors for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import HiveMindError, NotFoundError
from hivemind.royal_jelly.errors import (
    NeedsReviewError,
    PackageError,
    PromotionRefusedError,
    ReportFailedError,
    ReportMismatchError,
    RollbackRefusedError,
    RoyalJellyError,
    SpecRejectedError,
    ToolNotFoundError,
    ToolTransitionError,
    VersionExistsError,
    WrongCellError,
    WrongPrincipalError,
)

_REFUSALS = {
    ReportFailedError: "report_failed",
    ReportMismatchError: "report_mismatch",
    WrongPrincipalError: "wrong_principal",
    WrongCellError: "wrong_cell",
    VersionExistsError: "version_exists",
    NeedsReviewError: "needs_review",
}


@pytest.mark.parametrize(("error_class", "refusal"), list(_REFUSALS.items()))
def test_promotion_refusal_has_a_stable_code_and_is_caught_by_the_family_root(
    error_class: type[PromotionRefusedError], refusal: str
) -> None:
    error = error_class("nope")

    assert error.refusal == refusal
    assert error.code == f"hivemind.royal_jelly.promotion_refused.{refusal}"
    assert isinstance(error, PromotionRefusedError)
    assert isinstance(error, RoyalJellyError)
    assert isinstance(error, HiveMindError)


def test_every_error_code_is_unique() -> None:
    classes: list[type[RoyalJellyError]] = [
        RoyalJellyError,
        SpecRejectedError,
        PackageError,
        ToolTransitionError,
        ToolNotFoundError,
        RollbackRefusedError,
        PromotionRefusedError,
        *_REFUSALS,
    ]

    codes = [c.code for c in classes]

    assert len(set(codes)) == len(codes)


def test_spec_rejected_error_carries_every_reason_in_its_message() -> None:
    error = SpecRejectedError("echo_tool", ("first reason", "second reason"))

    assert error.reasons == ("first reason", "second reason")
    assert "first reason; second reason" in str(error)


def test_tool_transition_error_names_both_states() -> None:
    error = ToolTransitionError("RETIRED", "PROMOTED")

    assert "RETIRED" in str(error)
    assert "PROMOTED" in str(error)


def test_tool_not_found_is_both_a_not_found_and_a_royal_jelly_error() -> None:
    error = ToolNotFoundError("no such tool")

    assert isinstance(error, NotFoundError)
    assert isinstance(error, RoyalJellyError)
