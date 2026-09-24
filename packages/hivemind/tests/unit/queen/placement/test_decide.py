"""Tests for hivemind.queen.placement.decide: the Queen's pure Real-vs-Virtual-Cell decision.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.decide for the module under test.
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the rules these tests exercise.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity, make_footprint

from hivemind.cell import (
    CellCapabilities,
    CombShieldLevel,
    Isolation,
    OsFamily,
    RequestOrigin,
    TaskNeeds,
)
from hivemind.forage import ForageCapacity
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.hive.models import NIGHT_VEIL_IMAGE
from hivemind.queen.placement import (
    DormantCandidate,
    ForageView,
    Inventory,
    NightVeilConstraints,
    NightVeilHostingView,
    PlacementError,
    PlacementPolicy,
    Prefer,
    ProvisionVirtual,
    RealCandidate,
    ReuseDormant,
    ReuseReal,
    VirtualBackendCandidate,
    WaxMention,
    decide,
)
from waggle.clock import FakeClock
from waggle.ids import CellId, WardenId, new_cell_id, new_hive_id, new_warden_id

_CLOCK = FakeClock()


def _real(
    *,
    capabilities: CellCapabilities | None = None,
    is_hive_stand: bool = False,
    has_free_capacity: bool = True,
) -> RealCandidate:
    return RealCandidate(
        warden_id=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        capabilities=capabilities if capabilities is not None else make_capabilities(),
        comb_shield=CombShieldLevel.MEADOW,
        is_hive_stand=is_hive_stand,
        has_free_capacity=has_free_capacity,
    )


def _spec(
    *,
    image: str = "base-ubuntu",
    exoskeleton: bool = False,
    capacity: ForageCapacity | None = None,
) -> VirtualCellSpec:
    return VirtualCellSpec(
        image=image,
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.NONE,
        exoskeleton=exoskeleton,
        capacity=capacity if capacity is not None else make_capacity(),
        comb_shield=CombShieldLevel.MEADOW,
        hive_id=new_hive_id(_CLOCK),
    )


def _backend(
    *,
    name: str = "docker",
    headroom: int | None = None,
    image: str = "base-ubuntu",
    capacity: ForageCapacity | None = None,
) -> VirtualBackendCandidate:
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True, headroom=headroom)
    return VirtualBackendCandidate(
        name=name, capabilities=capabilities, specs=(_spec(image=image, capacity=capacity),)
    )


def _forage(
    *,
    request_origin: RequestOrigin = RequestOrigin.HUMAN,
    night_veil_hosting: NightVeilHostingView | None = None,
) -> ForageView:
    return ForageView(
        footprint=make_footprint(),
        request_origin=request_origin,
        night_veil_hosting=night_veil_hosting
        if night_veil_hosting is not None
        else (NightVeilHostingView()),
    )


def _night_veil_constraints() -> NightVeilConstraints:
    """A fully-configured [security] Night Veil profile.

    Used by every test that is not itself exercising check_night_veil's own "profile is not
    configured" violation.
    """
    return NightVeilConstraints(
        required_network_policy=NetworkPolicy.VPN_TOR,
        hive_stand_onion_address="abc123.onion",
        socks_proxy_url="socks5h://127.0.0.1:9050",
        locale_profile="C.UTF-8",
    )


def _policy(
    *,
    prefer: Prefer = "real",
    allow_hive_stand: bool = True,
    role_overrides: Mapping[str, Prefer] | None = None,
    night_veil: NightVeilConstraints | None = None,
) -> PlacementPolicy:
    return PlacementPolicy(
        prefer=prefer,
        allow_hive_stand=allow_hive_stand,
        role_overrides=role_overrides if role_overrides is not None else {},
        night_veil=night_veil,
    )


def _dormant(
    *, cell_id: CellId | None = None, warden_id: WardenId | None = None, image: str
) -> DormantCandidate:
    return DormantCandidate(
        cell_id=cell_id if cell_id is not None else new_cell_id(_CLOCK),
        warden_id=warden_id if warden_id is not None else new_warden_id(_CLOCK),
        image=image,
        comb_shield=CombShieldLevel.MEADOW,
    )


# ──────────────────────────────────────────────────────────────────────────────
# The v0 happy path still works, unchanged.
# ──────────────────────────────────────────────────────────────────────────────


def test_places_on_the_only_real_candidate_by_default() -> None:
    real = _real()

    placement = decide(TaskNeeds(), Inventory(real=(real,)), _forage(), _policy())

    assert placement == ReuseReal(real.cell_id, real.warden_id, placement.reason)


def test_no_candidates_at_all_raises_placement_error() -> None:
    with pytest.raises(PlacementError):
        decide(TaskNeeds(), Inventory(), _forage(), _policy())


# ──────────────────────────────────────────────────────────────────────────────
# Rule 1: isolation = REQUIRED is always Virtual.
# ──────────────────────────────────────────────────────────────────────────────


def test_isolation_required_is_virtual_regardless_of_prefer() -> None:
    real = _real()
    backend = _backend()
    inventory = Inventory(real=(real,), virtual_backends=(backend,))

    for prefer in ("real", "virtual"):
        placement = decide(
            TaskNeeds(isolation=Isolation.REQUIRED), inventory, _forage(), _policy(prefer=prefer)
        )
        assert isinstance(placement, ProvisionVirtual)


def test_isolation_required_with_no_backend_raises_placement_error_naming_eliminated_rules() -> (
    None
):
    real = _real()
    inventory = Inventory(real=(real,))  # No Virtual backend at all.

    with pytest.raises(PlacementError, match="isolation=REQUIRED"):
        decide(TaskNeeds(isolation=Isolation.REQUIRED), inventory, _forage(), _policy())


# ──────────────────────────────────────────────────────────────────────────────
# Rule 2: NIGHT_VEIL is always Virtual, fresh, forced VPN_TOR, never dormant.
# ──────────────────────────────────────────────────────────────────────────────


def test_night_veil_always_provisions_fresh_never_dormant_even_with_a_matching_image() -> None:
    backend = _backend(image="night-veil-ubuntu")
    # A dormant Cell with the same image sits in the pool, but Night Veil must never reuse it
    # (docs/adr/0029: "Overwintering Night Veil Cells... the tier forbids outright").
    dormant = _dormant(image="night-veil-ubuntu")
    inventory = Inventory(virtual_backends=(backend,), dormant=(dormant,))
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    policy = _policy(night_veil=_night_veil_constraints())

    placement = decide(needs, inventory, _forage(), policy)

    assert isinstance(placement, ProvisionVirtual)
    assert placement.spec.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert placement.spec.network_policy is NetworkPolicy.VPN_TOR
    assert placement.spec.network_allowlist == ()
    # Roadmap step 5.7a: stamped on the spec's own labels for a backend's label-only orphan sweep.
    assert placement.spec.labels["hivemind.comb_shield"] == "NIGHT_VEIL"


def test_night_veil_with_no_backend_raises_placement_error() -> None:
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    policy = _policy(night_veil=_night_veil_constraints())

    with pytest.raises(PlacementError, match="NIGHT_VEIL"):
        decide(needs, Inventory(), _forage(), policy)


def test_night_veil_requires_human_originated_request() -> None:
    backend = _backend(image="night-veil-ubuntu")
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    policy = _policy(night_veil=_night_veil_constraints())
    forage = _forage(request_origin=RequestOrigin.QUEEN)

    with pytest.raises(PlacementError, match="human-originated"):
        decide(needs, Inventory(virtual_backends=(backend,)), forage, policy)


def test_night_veil_requires_a_configured_security_profile() -> None:
    backend = _backend(image="night-veil-ubuntu")
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    policy = _policy(night_veil=None)  # No [security] Night Veil profile configured at all.

    with pytest.raises(PlacementError, match="tier profile"):
        decide(needs, Inventory(virtual_backends=(backend,)), _forage(), policy)


def test_night_veil_requires_a_profile_naming_an_onion_address_and_socks_proxy() -> None:
    backend = _backend(image="night-veil-ubuntu")
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    incomplete = NightVeilConstraints(
        required_network_policy=NetworkPolicy.VPN_TOR,
        hive_stand_onion_address="",  # Not configured: this is the violation under test.
        socks_proxy_url="socks5h://127.0.0.1:9050",
        locale_profile="C.UTF-8",
    )
    policy = _policy(night_veil=incomplete)

    with pytest.raises(PlacementError, match="hidden-service address"):
        decide(needs, Inventory(virtual_backends=(backend,)), _forage(), policy)


def test_night_veil_requires_every_model_slot_to_resolve_locally() -> None:
    backend = _backend(image="night-veil-ubuntu")
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    policy = _policy(night_veil=_night_veil_constraints())
    non_local = NightVeilHostingView(
        all_local=False, non_local_slots=("slot QUEEN: source hosted-api is not local",)
    )
    forage = _forage(night_veil_hosting=non_local)

    with pytest.raises(PlacementError, match="resolve locally"):
        decide(needs, Inventory(virtual_backends=(backend,)), forage, policy)


def test_night_veil_stamps_its_own_image_and_a_broken_rule_is_final() -> None:
    # Roadmap step 10.3a: the template names the ordinary image, and no backend runs VPN_TOR on it.
    backend = _backend(image="base-ubuntu")
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    inventory = Inventory(virtual_backends=(backend,))

    placement = decide(needs, inventory, _forage(), _policy(night_veil=_night_veil_constraints()))

    assert isinstance(placement, ProvisionVirtual)
    assert placement.spec.image == NIGHT_VEIL_IMAGE
    with pytest.raises(PlacementError) as refused:
        decide(needs, inventory, _forage(), _policy(night_veil=None))
    assert refused.value.final


# ──────────────────────────────────────────────────────────────────────────────
# Rule 3: a BLOCK Cell Wax excludes; allow_hive_stand=false excludes the Hive Stand.
# ──────────────────────────────────────────────────────────────────────────────


def test_a_block_on_the_hive_stand_turns_prefer_real_into_provision_virtual_naming_the_wax() -> (
    None
):
    """The roadmap's own exit criterion, verbatim."""
    hive_stand = _real(is_hive_stand=True)
    backend = _backend()
    wax = WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ00", text="disk nearly full")
    inventory = Inventory(
        real=(hive_stand,), virtual_backends=(backend,), blocked={hive_stand.cell_id: wax}
    )

    placement = decide(TaskNeeds(), inventory, _forage(), _policy(prefer="real"))

    assert isinstance(placement, ProvisionVirtual)
    assert wax.text in placement.reason
    assert wax.id in placement.reason


def test_clearing_the_block_restores_reuse_real() -> None:
    hive_stand = _real(is_hive_stand=True)
    backend = _backend()
    inventory = Inventory(real=(hive_stand,), virtual_backends=(backend,))  # No longer blocked.

    placement = decide(TaskNeeds(), inventory, _forage(), _policy(prefer="real"))

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == hive_stand.cell_id


def test_allow_hive_stand_false_excludes_it_even_as_the_only_candidate() -> None:
    hive_stand = _real(is_hive_stand=True)
    inventory = Inventory(real=(hive_stand,))

    with pytest.raises(PlacementError):
        decide(TaskNeeds(), inventory, _forage(), _policy(allow_hive_stand=False))


# ──────────────────────────────────────────────────────────────────────────────
# Rule 4: OS, network scopes, Exoskeleton.
# ──────────────────────────────────────────────────────────────────────────────


def test_os_mismatch_excludes_a_real_candidate() -> None:
    real = _real(capabilities=make_capabilities(os=OsFamily.LINUX))
    inventory = Inventory(real=(real,))

    with pytest.raises(PlacementError):
        decide(TaskNeeds(os=OsFamily.WINDOWS), inventory, _forage(), _policy())


def test_exoskeleton_needs_a_display_or_the_ability_to_start_one() -> None:
    no_display = _real(capabilities=make_capabilities(has_display=False, can_start_display=False))
    inventory = Inventory(real=(no_display,))

    with pytest.raises(PlacementError):
        decide(TaskNeeds(exoskeleton=True), inventory, _forage(), _policy())


def test_exoskeleton_is_satisfied_by_the_ability_to_start_a_display() -> None:
    can_start = _real(capabilities=make_capabilities(has_display=False, can_start_display=True))
    inventory = Inventory(real=(can_start,))

    placement = decide(TaskNeeds(exoskeleton=True), inventory, _forage(), _policy())

    assert isinstance(placement, ReuseReal)


def test_network_scopes_not_reachable_excludes_a_real_candidate() -> None:
    real = _real(capabilities=make_capabilities(network_scopes=("example.com",)))
    inventory = Inventory(real=(real,))

    with pytest.raises(PlacementError):
        decide(TaskNeeds(network_scopes=("other.example.com",)), inventory, _forage(), _policy())


# ──────────────────────────────────────────────────────────────────────────────
# Rule 5: Forage must cover the grant.
# ──────────────────────────────────────────────────────────────────────────────


def test_no_free_capacity_excludes_a_real_candidate() -> None:
    real = _real(has_free_capacity=False)
    inventory = Inventory(real=(real,))

    with pytest.raises(PlacementError):
        decide(TaskNeeds(), inventory, _forage(), _policy())


def test_no_backend_headroom_excludes_a_virtual_candidate() -> None:
    backend = _backend(headroom=0)
    inventory = Inventory(virtual_backends=(backend,))

    with pytest.raises(PlacementError):
        decide(TaskNeeds(), inventory, _forage(), _policy(prefer="virtual"))


def test_insufficient_spec_capacity_excludes_a_virtual_candidate() -> None:
    tiny_capacity = make_capacity(max_sub_bees=0)
    backend = _backend(capacity=tiny_capacity)
    inventory = Inventory(virtual_backends=(backend,))

    with pytest.raises(PlacementError):
        decide(TaskNeeds(), inventory, _forage(), _policy(prefer="virtual"))


# ──────────────────────────────────────────────────────────────────────────────
# Rule 6: prefer, CAUTION penalty, dormant before fresh provision, attachment order.
# ──────────────────────────────────────────────────────────────────────────────


def test_prefer_virtual_with_dormant_matching_image_returns_reuse_dormant() -> None:
    backend = _backend(image="base-ubuntu")
    dormant = _dormant(image="base-ubuntu")
    inventory = Inventory(virtual_backends=(backend,), dormant=(dormant,))

    placement = decide(TaskNeeds(), inventory, _forage(), _policy(prefer="virtual"))

    assert placement == ReuseDormant(dormant.cell_id, dormant.warden_id, placement.reason)


def test_prefer_virtual_with_no_dormant_match_provisions_fresh() -> None:
    backend = _backend(image="base-ubuntu")
    inventory = Inventory(virtual_backends=(backend,))

    placement = decide(TaskNeeds(), inventory, _forage(), _policy(prefer="virtual"))

    assert isinstance(placement, ProvisionVirtual)


def test_a_cautioned_real_candidate_ranks_behind_an_uncautioned_one() -> None:
    cautioned = _real()
    clean = _real()
    wax = WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ01", text="flaky network")
    inventory = Inventory(real=(cautioned, clean), cautioned={cautioned.cell_id: wax})

    placement = decide(TaskNeeds(), inventory, _forage(), _policy())

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == clean.cell_id


def test_a_cautioned_candidate_is_still_chosen_when_it_is_the_only_one() -> None:
    cautioned = _real()
    wax = WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ02", text="flaky network")
    inventory = Inventory(real=(cautioned,), cautioned={cautioned.cell_id: wax})

    placement = decide(TaskNeeds(), inventory, _forage(), _policy())

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == cautioned.cell_id


def test_attachment_order_breaks_every_remaining_tie() -> None:
    first = _real()
    second = _real()
    inventory = Inventory(real=(first, second))

    placement = decide(TaskNeeds(), inventory, _forage(), _policy())

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == first.cell_id


def test_prefer_virtual_falls_back_to_real_when_no_virtual_candidate_fits() -> None:
    real = _real()
    inventory = Inventory(real=(real,))  # No Virtual backend registered at all.

    placement = decide(TaskNeeds(), inventory, _forage(), _policy(prefer="virtual"))

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == real.cell_id


def test_role_override_takes_precedence_over_the_top_level_prefer() -> None:
    real = _real()
    backend = _backend()
    inventory = Inventory(real=(real,), virtual_backends=(backend,))
    policy = _policy(prefer="real", role_overrides={"drone": "virtual"})

    placement = decide(TaskNeeds(), inventory, _forage(), policy)

    assert isinstance(placement, ProvisionVirtual)
