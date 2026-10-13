"""Tests for hivemind.basket.clearance: the blob label rules, mirrored from the Honey Store's.

The floor and the Night Veil ceiling restate `hivemind.honey_store.clearance` (Layer 2 siblings do
not import each other), so each clause here names the clause of
`tests/unit/honey_store/test_clearance.py` it must agree with; that file is the source of truth.

Fits into the Hive:
    Mirrors src/hivemind/basket/clearance.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/honey_store/test_clearance.py, the rule's source of truth.
    - hivemind.basket.clearance for the module under test.
"""

from __future__ import annotations

import pytest
from builders.basket import make_blob

from hivemind.basket.clearance import (
    blob_clearance,
    blob_floor,
    may_receive,
    receipt_refusal,
    receiver_ceiling,
)
from hivemind.basket.models import BlobState
from hivemind.cell import CombShieldLevel, HoneyClearance, OsFamily
from hivemind.honey_store.clearance import intake_floor, intake_label
from hivemind.honey_store.models import NectarOrigin

_ALL_TIERS = list(CombShieldLevel)
_ALL_LABELS = list(HoneyClearance)


@pytest.mark.parametrize("tier", _ALL_TIERS)
def test_blob_floor_is_c2_from_a_borrowed_cell_and_c0_otherwise(tier: CombShieldLevel) -> None:
    # honey_store test_clearance: "intake_floor is always c2 for anything from a borrowed cell".
    assert blob_floor(True, tier) is HoneyClearance.C2
    assert blob_floor(False, tier) is HoneyClearance.C0


@pytest.mark.parametrize("borrowed", [True, False])
def test_blob_floor_agrees_with_the_honey_stores_intake_floor(borrowed: bool) -> None:
    # The restatement must never drift: for a non-human, non-watch origin the two are equal.
    honey = intake_floor(NectarOrigin.BEE, borrowed)

    assert blob_floor(borrowed, CombShieldLevel.MEADOW) is honey


@pytest.mark.parametrize("declared", [*_ALL_LABELS, None])
@pytest.mark.parametrize("borrowed", [True, False])
def test_blob_clearance_is_the_higher_of_declared_and_floor(
    declared: HoneyClearance | None, borrowed: bool
) -> None:
    result = blob_clearance(declared, borrowed, CombShieldLevel.MEADOW)

    expected = intake_label(declared, NectarOrigin.BEE, borrowed, HoneyClearance.C0)
    assert result is expected
    assert result.rank >= blob_floor(borrowed, CombShieldLevel.MEADOW).rank
    assert declared is None or result.rank >= declared.rank


def test_a_declared_c1_from_a_borrowed_cell_is_raised_to_c2() -> None:
    assert blob_clearance(HoneyClearance.C1, True, CombShieldLevel.MEADOW) is HoneyClearance.C2


def test_receiver_ceiling_caps_night_veil_at_c1_and_nothing_else() -> None:
    # honey_store test_clearance: reader_ceiling's "c1 for night veil, c2 otherwise" default.
    assert receiver_ceiling(CombShieldLevel.NIGHT_VEIL) is HoneyClearance.C1
    assert receiver_ceiling(CombShieldLevel.MEADOW) is HoneyClearance.C2
    assert receiver_ceiling(CombShieldLevel.PROPOLIS) is HoneyClearance.C2


def test_a_real_cell_blob_never_reaches_a_night_veil_cell() -> None:
    blob = make_blob(clearance=blob_clearance(None, True, CombShieldLevel.MEADOW))

    ok, reason = may_receive(blob, CombShieldLevel.NIGHT_VEIL, OsFamily.LINUX)

    assert not ok and "ceiling" in reason


def test_a_public_blob_may_be_received_anywhere_on_any_platform_when_untagged() -> None:
    blob = make_blob(clearance=HoneyClearance.C0)

    for tier in _ALL_TIERS:
        assert may_receive(blob, tier, OsFamily.WINDOWS) == (True, "")


def test_a_tainted_blob_is_refused_to_every_cell() -> None:
    blob = make_blob(tainted=True)

    refusal = receipt_refusal(blob, HoneyClearance.C2, OsFamily.LINUX)
    assert refusal is not None and refusal.code == "tainted"


def test_a_retired_blob_is_refused_before_anything_else() -> None:
    blob = make_blob(state=BlobState.RETIRED, tainted=True)

    refusal = receipt_refusal(blob, HoneyClearance.C2, OsFamily.LINUX)

    assert refusal is not None and refusal.code == "retired"


def test_a_platform_tagged_blob_is_refused_to_another_operating_system() -> None:
    blob = make_blob(platform_tags=(OsFamily.LINUX,))

    wrong = receipt_refusal(blob, HoneyClearance.C2, OsFamily.WINDOWS)
    right = receipt_refusal(blob, HoneyClearance.C2, OsFamily.LINUX)

    assert wrong is not None and wrong.code == "platform"
    assert right is None
