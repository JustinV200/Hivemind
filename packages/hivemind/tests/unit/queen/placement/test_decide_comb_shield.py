"""Tests for hivemind.queen.placement.decide on a Real Cell's tier (roadmap step 11.8a).

A Real Cell's Comb Shield tier is its operator's, set at enrolment and never changed by placement,
so a task that needs PROPOLIS must never land on a MEADOW device (`rules.fits_comb_shield`), and a
NIGHT_VEIL task never reaches a Real Cell at all: a goal whose ceiling lacks `cell:virtual` is
denied at the placement point instead. Split from `test_decide.py` (which sits near the test file
cap) the same way `test_decide_goal_ceiling.py` is: these exercise the same `decide`, one rule.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.rules.fits_comb_shield for the rule this pins down.
    - codingrules section 8.7 for "Comb Shield is a Cell property" and the Night Veil rules.
"""

from __future__ import annotations

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity, make_footprint

from hivemind.cell import CellKind, CombShieldLevel, Isolation, TaskNeeds
from hivemind.guard import CapabilitySet
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.queen.placement import (
    ForageView,
    Inventory,
    NightVeilConstraints,
    PlacementError,
    PlacementPolicy,
    ProvisionVirtual,
    RealCandidate,
    ReuseReal,
    VirtualBackendCandidate,
    decide,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_warden_id

_CLOCK = FakeClock()
_PROPOLIS = TaskNeeds(comb_shield=CombShieldLevel.PROPOLIS)
_PREFER_REAL = PlacementPolicy(prefer="real", allow_hive_stand=True, role_overrides={})


def _device(tier: CombShieldLevel) -> RealCandidate:
    """An attached Swarm device (a Real Cell that is not the Hive Stand) at `tier`."""
    return RealCandidate(
        warden_id=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        capabilities=make_capabilities(),
        comb_shield=tier,
        is_hive_stand=False,
        has_free_capacity=True,
        kind=CellKind.REAL,
    )


def _spec(tier: CombShieldLevel) -> VirtualCellSpec:
    """A spec that provisions its Cell at `tier`, from an image named after the tier."""
    return VirtualCellSpec(
        image=f"base-ubuntu-{tier.name.lower()}",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.VPN_TOR
        if tier is CombShieldLevel.NIGHT_VEIL
        else NetworkPolicy.NONE,
        exoskeleton=False,
        capacity=make_capacity(),
        comb_shield=tier,
        hive_id=new_hive_id(_CLOCK),
    )


def _backend(*tiers: CombShieldLevel) -> VirtualBackendCandidate:
    """A Docker-like backend with one spec per tier in `tiers` (MEADOW alone when none is given).

    It can also hold a Night Veil Cell, which Night Veil's own path stamps on a spec it chooses.
    """
    specs = tuple(_spec(tier) for tier in tiers or (CombShieldLevel.MEADOW,))
    capabilities = BackendCapabilities(
        can_snapshot=False, can_pause=True, headroom=None, can_night_veil=True
    )
    return VirtualBackendCandidate(name="docker", capabilities=capabilities, specs=specs)


def _view(*ceiling: str) -> ForageView:
    """A ForageView whose goal carries `ceiling`, or no ceiling at all when none is given."""
    goal = CapabilitySet.parse(*ceiling) if ceiling else None
    return ForageView(footprint=make_footprint(), goal_capabilities=goal)


def test_a_propolis_task_never_lands_on_a_meadow_device() -> None:
    meadow = _device(CombShieldLevel.MEADOW)

    with pytest.raises(PlacementError) as caught:
        decide(_PROPOLIS, Inventory(real=(meadow,)), _view(), _PREFER_REAL)

    # The reason is recorded like every other exclusion: the Cell, the rule and both tiers.
    assert f"Cell {meadow.cell_id}: Comb Shield mismatch (needs PROPOLIS, has MEADOW)" in str(
        caught.value
    )
    assert caught.value.denied == ()  # The tier, not the goal's ceiling, left no candidate.


def test_a_propolis_task_takes_the_propolis_device_even_when_a_meadow_one_is_attached_first() -> (
    None
):
    meadow, propolis = _device(CombShieldLevel.MEADOW), _device(CombShieldLevel.PROPOLIS)

    placement = decide(_PROPOLIS, Inventory(real=(meadow, propolis)), _view(), _PREFER_REAL)

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == propolis.cell_id


def test_a_plain_task_still_lands_on_a_propolis_device() -> None:
    propolis = _device(CombShieldLevel.PROPOLIS)

    placement = decide(TaskNeeds(), Inventory(real=(propolis,)), _view(), _PREFER_REAL)

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == propolis.cell_id


def test_a_propolis_task_falls_back_to_a_propolis_virtual_spec_naming_the_tier_mismatch() -> None:
    meadow = _device(CombShieldLevel.MEADOW)
    backend = _backend(CombShieldLevel.MEADOW, CombShieldLevel.PROPOLIS)
    inventory = Inventory(real=(meadow,), virtual_backends=(backend,))

    placement = decide(_PROPOLIS, inventory, _view(), _PREFER_REAL)

    # The PROPOLIS spec, whose Cell is attested before it is ready (ADR-0057), never the MEADOW one.
    assert isinstance(placement, ProvisionVirtual)
    assert placement.spec.comb_shield is CombShieldLevel.PROPOLIS
    assert "Comb Shield mismatch (needs PROPOLIS, has MEADOW)" in placement.reason


def test_a_propolis_task_is_never_given_a_meadow_virtual_cell() -> None:
    meadow = _device(CombShieldLevel.MEADOW)
    inventory = Inventory(real=(meadow,), virtual_backends=(_backend(CombShieldLevel.MEADOW),))

    with pytest.raises(PlacementError) as caught:
        decide(_PROPOLIS, inventory, _view(), _PREFER_REAL)

    # Both sides say why: the device's tier and the spec's, each a mismatch, and no capability.
    message = str(caught.value)
    assert "Comb Shield mismatch (needs PROPOLIS, has MEADOW)" in message
    assert "image base-ubuntu-meadow: Comb Shield mismatch (needs PROPOLIS, provisions MEADOW)" in (
        message
    )
    assert caught.value.denied == ()


@pytest.mark.parametrize("needs", [TaskNeeds(), _PROPOLIS])
def test_a_night_veil_spec_is_never_taken_outside_night_veil_s_own_path(needs: TaskNeeds) -> None:
    inventory = Inventory(virtual_backends=(_backend(CombShieldLevel.NIGHT_VEIL),))

    with pytest.raises(PlacementError) as caught:
        decide(needs, inventory, _view(), _PREFER_REAL)

    assert "provisions NIGHT_VEIL" in str(caught.value)


def test_a_plain_task_may_take_a_propolis_virtual_spec() -> None:
    inventory = Inventory(virtual_backends=(_backend(CombShieldLevel.PROPOLIS),))

    placement = decide(TaskNeeds(), inventory, _view(), _PREFER_REAL)

    assert isinstance(placement, ProvisionVirtual)
    assert placement.spec.comb_shield is CombShieldLevel.PROPOLIS


def test_a_night_veil_task_whose_ceiling_allows_only_real_cells_is_denied_not_placed() -> None:
    device = _device(CombShieldLevel.MEADOW)
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    policy = PlacementPolicy(
        prefer="real",
        allow_hive_stand=True,
        role_overrides={},
        night_veil=NightVeilConstraints(
            required_network_policy=NetworkPolicy.VPN_TOR,
            hive_stand_onion_address="abc123.onion",
            socks_proxy_url="socks5h://127.0.0.1:9050",
            locale_profile="C.UTF-8",
        ),
    )
    # The goal may use that very device and any tier, but no Virtual Cell: Night Veil's only home.
    ceiling = _view(f"cell:real:{device.cell_id}", "cell:comb_shield:*")

    with pytest.raises(PlacementError) as caught:
        decide(needs, Inventory(real=(device,), virtual_backends=(_backend(),)), ceiling, policy)

    assert [str(capability) for capability in caught.value.denied] == ["cell:virtual"]
    assert "NIGHT_VEIL placement refused: goal lacks cell:virtual" in str(caught.value)
