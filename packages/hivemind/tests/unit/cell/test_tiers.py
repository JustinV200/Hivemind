"""Tests for hivemind.cell.tiers: AccessLevel, CombShieldLevel, HoneyClearance.

Fits into the Hive:
    Mirrors src/hivemind/cell/tiers.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.tiers for the module under test.
"""

from __future__ import annotations

from enum import Enum

import pytest

from hivemind.cell.tiers import AccessLevel, CombShieldLevel, HoneyClearance
from waggle.messages import AccessLevel as WireAccessLevel
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages import HoneyClearance as WireHoneyClearance

# One (hivemind enum, wire enum) pair per tier dimension, shared by the parametrised sync test.
_ENUM_PAIRS: list[tuple[type[Enum], type[Enum]]] = [
    (AccessLevel, WireAccessLevel),
    (CombShieldLevel, WireCombShieldLevel),
    (HoneyClearance, WireHoneyClearance),
]


@pytest.mark.parametrize(
    ("hivemind_enum", "wire_enum"), _ENUM_PAIRS, ids=[pair[0].__name__ for pair in _ENUM_PAIRS]
)
def test_tier_enum_mirrors_the_wire_enum_member_for_member(
    hivemind_enum: type[Enum], wire_enum: type[Enum]
) -> None:
    hivemind_names = [member.name for member in hivemind_enum]
    wire_names = [member.name for member in wire_enum]

    assert hivemind_names == wire_names
    assert [member.value for member in hivemind_enum] == [member.value for member in wire_enum]


def test_access_level_rank_orders_read_only_scratch_full() -> None:
    assert AccessLevel.READ_ONLY.rank < AccessLevel.SCRATCH.rank < AccessLevel.FULL.rank


def test_honey_clearance_rank_orders_c0_c1_c2() -> None:
    assert HoneyClearance.C0.rank < HoneyClearance.C1.rank < HoneyClearance.C2.rank


@pytest.mark.parametrize("member", list(AccessLevel))
def test_access_level_round_trips_through_wire_conversion(member: AccessLevel) -> None:
    wire = member.to_wire()

    assert wire is WireAccessLevel(member.value)
    assert AccessLevel.from_wire(wire) is member


@pytest.mark.parametrize("member", list(CombShieldLevel))
def test_comb_shield_level_round_trips_through_wire_conversion(member: CombShieldLevel) -> None:
    wire = member.to_wire()

    assert wire is WireCombShieldLevel(member.value)
    assert CombShieldLevel.from_wire(wire) is member


@pytest.mark.parametrize("member", list(HoneyClearance))
def test_honey_clearance_round_trips_through_wire_conversion(member: HoneyClearance) -> None:
    wire = member.to_wire()

    assert wire is WireHoneyClearance(member.value)
    assert HoneyClearance.from_wire(wire) is member
