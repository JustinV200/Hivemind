"""Tests for hivemind.honey_store.clearance: intake floor/label, raise_label, ceiling, lowering.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/clearance.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.clearance for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.clearance import (
    LabelApprover,
    check_lowering,
    intake_floor,
    intake_label,
    raise_label,
    reader_ceiling,
)
from hivemind.honey_store.errors import LabelLoweringError
from hivemind.honey_store.models import NectarOrigin
from hivemind.manifest.schema.security.tiers import ClearanceMatrix
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages import HoneyClearance as WireHoneyClearance

# ──────────────────────────────────────────────────────────────────────────────
# intake_floor
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("origin", [NectarOrigin.HUMAN, NectarOrigin.WATCH])
@pytest.mark.parametrize("from_borrowed_cell", [True, False])
def test_intake_floor_is_always_c2_for_human_and_watch(
    origin: NectarOrigin, from_borrowed_cell: bool
) -> None:
    assert intake_floor(origin, from_borrowed_cell) is HoneyClearance.C2


@pytest.mark.parametrize("origin", list(NectarOrigin))
def test_intake_floor_is_c2_for_anything_from_a_borrowed_cell(origin: NectarOrigin) -> None:
    # Whatever route it took: a Real Cell's Bee Bread or Cell Wax is the operator's machine too.
    assert intake_floor(origin, True) is HoneyClearance.C2


@pytest.mark.parametrize(
    "origin",
    [
        NectarOrigin.BEE,
        NectarOrigin.TASK_OUTCOME,
        NectarOrigin.BEE_BREAD,
        NectarOrigin.CELL_WAX,
    ],
)
def test_intake_floor_is_c0_not_from_a_borrowed_cell(origin: NectarOrigin) -> None:
    assert intake_floor(origin, False) is HoneyClearance.C0


# ──────────────────────────────────────────────────────────────────────────────
# intake_label
# ──────────────────────────────────────────────────────────────────────────────


def test_intake_label_uses_declared_when_it_is_above_the_floor() -> None:
    result = intake_label(HoneyClearance.C1, NectarOrigin.BEE, False, HoneyClearance.C0)

    assert result is HoneyClearance.C1


def test_intake_label_raises_declared_to_the_floor_when_below_it() -> None:
    result = intake_label(HoneyClearance.C0, NectarOrigin.HUMAN, False, HoneyClearance.C0)

    assert result is HoneyClearance.C2  # HUMAN's floor is C2, above the C0 declared.


def test_intake_label_uses_default_label_when_nothing_declared() -> None:
    result = intake_label(None, NectarOrigin.BEE, False, HoneyClearance.C1)

    assert result is HoneyClearance.C1


def test_intake_label_raises_the_default_to_the_floor_too() -> None:
    result = intake_label(None, NectarOrigin.WATCH, False, HoneyClearance.C0)

    assert result is HoneyClearance.C2


# ──────────────────────────────────────────────────────────────────────────────
# raise_label
# ──────────────────────────────────────────────────────────────────────────────


def test_raise_label_returns_the_higher_of_the_two() -> None:
    assert raise_label(HoneyClearance.C0, HoneyClearance.C2) is HoneyClearance.C2
    assert raise_label(HoneyClearance.C2, HoneyClearance.C0) is HoneyClearance.C2


def test_raise_label_returns_current_when_equal() -> None:
    assert raise_label(HoneyClearance.C1, HoneyClearance.C1) is HoneyClearance.C1


# ──────────────────────────────────────────────────────────────────────────────
# reader_ceiling
# ──────────────────────────────────────────────────────────────────────────────


def test_reader_ceiling_is_the_lowest_of_the_three() -> None:
    matrix = {
        WireCombShieldLevel.MEADOW: ClearanceMatrix(
            read=(WireHoneyClearance.C0, WireHoneyClearance.C1, WireHoneyClearance.C2),
            write=(),
        )
    }

    result = reader_ceiling(HoneyClearance.C2, HoneyClearance.C1, CombShieldLevel.MEADOW, matrix)

    assert result is HoneyClearance.C1  # principal (C1) is the tightest of the three.


def test_reader_ceiling_defaults_to_c1_for_night_veil_with_no_matrix_row() -> None:
    result = reader_ceiling(HoneyClearance.C2, HoneyClearance.C2, CombShieldLevel.NIGHT_VEIL, {})

    assert result is HoneyClearance.C1


def test_reader_ceiling_defaults_to_c2_for_other_tiers_with_no_matrix_row() -> None:
    result = reader_ceiling(HoneyClearance.C2, HoneyClearance.C2, CombShieldLevel.MEADOW, {})

    assert result is HoneyClearance.C2


def test_reader_ceiling_uses_the_tiers_own_matrix_row_as_a_ceiling() -> None:
    matrix = {
        WireCombShieldLevel.PROPOLIS: ClearanceMatrix(
            read=(WireHoneyClearance.C0, WireHoneyClearance.C1), write=()
        )
    }

    result = reader_ceiling(HoneyClearance.C2, HoneyClearance.C2, CombShieldLevel.PROPOLIS, matrix)

    assert result is HoneyClearance.C1  # The tier's own row caps it below what was requested.


# ──────────────────────────────────────────────────────────────────────────────
# check_lowering
# ──────────────────────────────────────────────────────────────────────────────


def test_check_lowering_accepts_a_genuine_lowering_with_an_approver() -> None:
    check_lowering(HoneyClearance.C2, HoneyClearance.C1, LabelApprover.JUDGE)  # Does not raise.


def test_check_lowering_rejects_a_target_that_is_not_lower() -> None:
    with pytest.raises(LabelLoweringError):
        check_lowering(HoneyClearance.C1, HoneyClearance.C1, LabelApprover.HUMAN)


def test_check_lowering_rejects_a_target_that_is_higher() -> None:
    with pytest.raises(LabelLoweringError):
        check_lowering(HoneyClearance.C0, HoneyClearance.C1, LabelApprover.HUMAN)


def test_check_lowering_rejects_no_approver() -> None:
    with pytest.raises(LabelLoweringError):
        check_lowering(HoneyClearance.C2, HoneyClearance.C0, None)
