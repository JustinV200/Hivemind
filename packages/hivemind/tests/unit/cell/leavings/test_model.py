"""Tests for hivemind.cell.leavings.model: Leaving and ApprovedBy's own field validation.

Fits into the Hive:
    Mirrors src/hivemind/cell/leavings/model.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.leavings.model for the module under test.
"""

from __future__ import annotations

import pytest
from builders.cells import make_leaving
from pydantic import ValidationError

from hivemind.cell.leavings.model import ApprovedBy, Leaving


def test_make_leaving_builds_a_valid_leaving() -> None:
    leaving = make_leaving()

    assert leaving.approved_by is ApprovedBy.POLICY
    assert leaving.removed_at is None
    assert leaving.prior is None


def test_leaving_is_frozen() -> None:
    leaving = make_leaving()

    with pytest.raises(ValidationError):
        leaving.reason = "changed"  # type: ignore[misc]


def test_leaving_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        make_leaving(unknown="nope")


def test_leaving_rejects_a_sha256_of_the_wrong_length() -> None:
    with pytest.raises(ValidationError):
        make_leaving(sha256="too-short")


def test_leaving_rejects_a_negative_size() -> None:
    with pytest.raises(ValidationError):
        make_leaving(size=-1)


def test_leaving_round_trips_through_json() -> None:
    leaving = make_leaving(prior=b"old content")

    restored = Leaving.model_validate_json(leaving.model_dump_json())

    assert restored == leaving
