"""Tests for hivemind.honey_store.lowering.rules: which Nectar a judge may lower, and to what.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/lowering/rules.py (codingrules section 3). Every clause of
    ADR-0034's eligibility list is stated once, against the one eligible Nectar `_eligible`
    builds, with exactly one field changed.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.lowering.rules for the module under test.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the rule.
"""

from __future__ import annotations

import pytest
from builders.honey import make_nectar

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.clearance import HUMAN_ONLY_ORIGINS
from hivemind.honey_store.lowering.rules import lowering_target
from hivemind.honey_store.models import Nectar, NectarOrigin, NectarState


def _eligible(**overrides: object) -> Nectar:
    """A ripened C2 Hive Stand deposit declared C1 and read C0: eligible, target C1."""
    fields: dict[str, object] = {
        "state": NectarState.RIPENED,
        "clearance": HoneyClearance.C2,
        "declared_clearance": HoneyClearance.C1,
        "floor_clearance": HoneyClearance.C2,
        "ripener_clearance": HoneyClearance.C0,
    }
    fields.update(overrides)
    return make_nectar(None, **fields)


def test_lowering_target_is_the_declared_label_when_the_reading_is_lower() -> None:
    # A judge never takes a label below what any depositor declared.
    assert lowering_target(_eligible()) is HoneyClearance.C1


def test_lowering_target_is_the_reading_when_it_is_higher_than_the_declared_label() -> None:
    nectar = _eligible(declared_clearance=HoneyClearance.C0, ripener_clearance=HoneyClearance.C1)

    assert lowering_target(nectar) is HoneyClearance.C1


def test_lowering_target_can_reach_c0_when_both_facts_say_c0() -> None:
    nectar = _eligible(declared_clearance=HoneyClearance.C0, ripener_clearance=HoneyClearance.C0)

    assert lowering_target(nectar) is HoneyClearance.C0


def test_lowering_target_lowers_a_c1_label_held_up_by_the_floor_alone() -> None:
    # A floor of C1 is not a shipped provenance floor, but the rule is about ranks, not values.
    nectar = _eligible(
        clearance=HoneyClearance.C1,
        declared_clearance=HoneyClearance.C0,
        floor_clearance=HoneyClearance.C1,
        ripener_clearance=HoneyClearance.C0,
    )

    assert lowering_target(nectar) is HoneyClearance.C0


@pytest.mark.parametrize(
    "state", [NectarState.RECEIVED, NectarState.EPHEMERAL, NectarState.DISCARDED]
)
def test_lowering_target_refuses_a_nectar_that_is_not_ripened(state: NectarState) -> None:
    assert lowering_target(_eligible(state=state)) is None


def test_lowering_target_refuses_a_tainted_nectar() -> None:
    assert lowering_target(_eligible(tainted=True)) is None


def test_lowering_target_refuses_a_night_veil_origin_tier() -> None:
    nectar = _eligible(origin_tier=CombShieldLevel.NIGHT_VEIL)

    assert lowering_target(nectar) is None


def test_lowering_target_allows_a_propolis_origin_tier() -> None:
    # Only the Night Veil tier is excluded; a VPN-only Cell's deposits are ordinary.
    assert lowering_target(_eligible(origin_tier=CombShieldLevel.PROPOLIS)) is HoneyClearance.C1


@pytest.mark.parametrize("origin", [NectarOrigin.HUMAN, NectarOrigin.WATCH])
def test_lowering_target_refuses_human_and_watch_origins(origin: NectarOrigin) -> None:
    # Their C2 is the content's own nature: a person's words, or the operator's machine.
    assert origin in HUMAN_ONLY_ORIGINS

    assert lowering_target(_eligible(origin=origin)) is None


@pytest.mark.parametrize(
    "origin",
    [NectarOrigin.BEE, NectarOrigin.TASK_OUTCOME, NectarOrigin.BEE_BREAD, NectarOrigin.CELL_WAX],
)
def test_lowering_target_allows_every_other_origin(origin: NectarOrigin) -> None:
    assert lowering_target(_eligible(origin=origin)) is HoneyClearance.C1


@pytest.mark.parametrize(
    "legacy",
    [
        {"declared_clearance": None},
        {"floor_clearance": None},
        {"ripener_clearance": None},
        {"declared_clearance": None, "floor_clearance": None, "ripener_clearance": None},
    ],
)
def test_lowering_target_refuses_a_row_with_any_unknown_fact(legacy: dict[str, object]) -> None:
    # A row written before ADR-0034 (or ripened heuristically) has facts nobody knows.
    assert lowering_target(_eligible(**legacy)) is None


def test_lowering_target_refuses_when_a_merge_raised_the_declared_label_to_the_label() -> None:
    # A later depositor declared C2: the label is no longer held up by the floor alone.
    nectar = _eligible(declared_clearance=HoneyClearance.C2)

    assert lowering_target(nectar) is None


def test_lowering_target_refuses_a_label_the_floor_does_not_reach() -> None:
    # Something other than the floor raised this label; lowering would undo that raise.
    nectar = _eligible(floor_clearance=HoneyClearance.C0, declared_clearance=HoneyClearance.C0)

    assert lowering_target(nectar) is None


def test_lowering_target_refuses_a_reading_equal_to_the_label() -> None:
    # The Ripener agreed with the label: there is nothing to propose.
    assert lowering_target(_eligible(ripener_clearance=HoneyClearance.C2)) is None


def test_lowering_target_refuses_a_nectar_already_at_its_declared_label() -> None:
    # A Virtual Cell deposit: its label is exactly what was declared, the target equal to it.
    nectar = _eligible(
        clearance=HoneyClearance.C1,
        declared_clearance=HoneyClearance.C1,
        floor_clearance=HoneyClearance.C0,
        ripener_clearance=HoneyClearance.C0,
    )

    assert lowering_target(nectar) is None


def test_lowering_target_refuses_a_c0_label() -> None:
    # Nothing ranks below C0, so no reading or declared label can be lower than it.
    nectar = _eligible(
        clearance=HoneyClearance.C0,
        declared_clearance=HoneyClearance.C0,
        floor_clearance=HoneyClearance.C0,
        ripener_clearance=HoneyClearance.C0,
    )

    assert lowering_target(nectar) is None


def test_lowering_target_never_returns_a_label_at_or_above_the_current_one() -> None:
    # Exhaustive over every combination of the three facts on a C2 label.
    labels = list(HoneyClearance)
    for declared in labels:
        for floor in labels:
            for reading in labels:
                nectar = _eligible(
                    declared_clearance=declared, floor_clearance=floor, ripener_clearance=reading
                )
                target = lowering_target(nectar)
                if target is not None:
                    assert target.rank < HoneyClearance.C2.rank
                    assert target.rank >= declared.rank
