"""Tests for hivemind.queen.placement.decide's goal ceiling (roadmap step 10.3, ADR-0031).

A goal's capability set is a ceiling on where its tasks may run: the Hive Stand needs
`cell:hive_stand`, any other Real Cell `cell:real:<cell id>`, a Virtual Cell `cell:virtual`, and a
Cell at a tier `cell:comb_shield:<tier>`. Split from `test_decide.py` (which sits near the test
file cap) the same way `test_decide_properties.py` is: these exercise the same `decide`, one rule.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.rules for goal_lacks, placement_needs and virtual_placement_needs.
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "A goal carries a
      ceiling".
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity, make_footprint

from hivemind.cell import CellKind, CombShieldLevel, TaskNeeds
from hivemind.guard import Capability, CapabilitySet
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.queen.placement import (
    ForageView,
    Inventory,
    PlacementError,
    PlacementPolicy,
    ProvisionVirtual,
    RealCandidate,
    ReuseReal,
    VirtualBackendCandidate,
    decide,
    rules,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_warden_id

_CLOCK = FakeClock()
_PREFER_REAL = PlacementPolicy(prefer="real", allow_hive_stand=True, role_overrides={})


def _real(*, is_hive_stand: bool = False, kind: CellKind = CellKind.REAL) -> RealCandidate:
    return RealCandidate(
        warden_id=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        capabilities=make_capabilities(),
        comb_shield=CombShieldLevel.MEADOW,
        is_hive_stand=is_hive_stand,
        has_free_capacity=True,
        kind=kind,
    )


def _backend() -> VirtualBackendCandidate:
    spec = VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.NONE,
        exoskeleton=False,
        capacity=make_capacity(),
        comb_shield=CombShieldLevel.MEADOW,
        hive_id=new_hive_id(_CLOCK),
    )
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None)
    return VirtualBackendCandidate(name="docker", capabilities=capabilities, specs=(spec,))


def _goal(*capabilities: str) -> ForageView:
    return ForageView(
        footprint=make_footprint(), goal_capabilities=CapabilitySet.parse(*capabilities)
    )


def test_no_ceiling_leaves_the_hive_stand_a_candidate() -> None:
    stand = _real(is_hive_stand=True)
    unbounded = ForageView(footprint=make_footprint())

    placement = decide(TaskNeeds(), Inventory(real=(stand,)), unbounded, _PREFER_REAL)

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == stand.cell_id


def test_a_goal_without_cell_hive_stand_lands_virtual_under_prefer_real() -> None:
    inventory = Inventory(real=(_real(is_hive_stand=True),), virtual_backends=(_backend(),))
    goal = _goal("cell:virtual", "cell:comb_shield:*")

    placement = decide(TaskNeeds(), inventory, goal, _PREFER_REAL)

    assert isinstance(placement, ProvisionVirtual)
    assert "prefer=real found no Real Cell" in placement.reason
    assert "Hive Stand: goal lacks cell:hive_stand" in placement.reason


def test_a_named_real_cell_needs_its_own_cell_real_scope() -> None:
    mine, other = _real(), _real()
    goal = _goal(f"cell:real:{mine.cell_id}", "cell:comb_shield:*")

    placement = decide(TaskNeeds(), Inventory(real=(other, mine)), goal, _PREFER_REAL)

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == mine.cell_id


def test_a_goal_allowing_no_candidate_fails_placement_naming_what_it_lacked() -> None:
    inventory = Inventory(real=(_real(is_hive_stand=True),), virtual_backends=(_backend(),))
    goal = _goal("tool:*")  # Neither side's placement capability.

    with pytest.raises(PlacementError) as caught:
        decide(TaskNeeds(), inventory, goal, _PREFER_REAL)

    denied = {str(capability) for capability in caught.value.denied}
    assert denied == {"cell:hive_stand", "cell:virtual"}


def test_a_tier_the_goal_lacks_excludes_the_cell_at_it() -> None:
    goal = _goal("cell:hive_stand", "cell:comb_shield:propolis")  # The Cell is at MEADOW.

    with pytest.raises(PlacementError) as caught:
        decide(TaskNeeds(), Inventory(real=(_real(is_hive_stand=True),)), goal, _PREFER_REAL)

    assert [str(capability) for capability in caught.value.denied] == ["cell:comb_shield:meadow"]


def test_a_failure_the_goal_did_not_cause_names_no_denial() -> None:
    # Capacity, not the ceiling, left no candidate: nothing is the goal's to be refused.
    stand = _real(is_hive_stand=True)
    full = dataclasses.replace(stand, has_free_capacity=False)

    goal = _goal("cell:hive_stand", "cell:comb_shield:*")

    with pytest.raises(PlacementError) as caught:
        decide(TaskNeeds(), Inventory(real=(full,)), goal, _PREFER_REAL)

    assert caught.value.denied == ()


def test_placement_needs_read_the_attached_cells_own_source() -> None:
    virtual = _real(kind=CellKind.VIRTUAL)

    assert rules.placement_needs(virtual)[0] == Capability.parse("cell:virtual")
    assert rules.placement_needs(_real(is_hive_stand=True))[0] == Capability.parse(
        "cell:hive_stand"
    )
