"""Tests for hivemind.queen.placement.rules: one small function per ADR-0028 rule.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/rules.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.rules for the module under test.
"""

from __future__ import annotations

from builders.cells import make_capabilities
from builders.forage import make_capacity, make_footprint

from hivemind.cell import CombShieldLevel, Isolation, OsFamily, TaskNeeds
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.queen.placement import PlacementPolicy, RealCandidate, WaxMention, rules
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_warden_id

_CLOCK = FakeClock()


def _spec(**overrides: object) -> VirtualCellSpec:
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 1.0,
        "memory_bytes": 1024**3,
        "disk_bytes": 8 * 1024**3,
        "network_policy": NetworkPolicy.NONE,
        "exoskeleton": False,
        "capacity": make_capacity(),
        "comb_shield": CombShieldLevel.MEADOW,
        "hive_id": new_hive_id(_CLOCK),
    }
    fields.update(overrides)
    return VirtualCellSpec.model_validate(fields)


def test_isolation_requires_virtual_true_for_required() -> None:
    assert rules.isolation_requires_virtual(TaskNeeds(isolation=Isolation.REQUIRED)) is True


def test_isolation_requires_virtual_false_otherwise() -> None:
    assert rules.isolation_requires_virtual(TaskNeeds(isolation=Isolation.PREFERRED)) is False


def test_night_veil_requires_virtual_true_for_night_veil() -> None:
    needs = TaskNeeds(isolation=Isolation.REQUIRED, comb_shield=CombShieldLevel.NIGHT_VEIL)
    assert rules.night_veil_requires_virtual(needs) is True


def test_night_veil_requires_virtual_false_for_meadow() -> None:
    assert rules.night_veil_requires_virtual(TaskNeeds()) is False


def test_excluded_by_block_wax_true_when_cell_id_is_blocked() -> None:
    cell_id = new_cell_id(_CLOCK)
    blocked = {cell_id: WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ00", text="reason")}
    assert rules.excluded_by_block_wax(cell_id, blocked) is True


def test_excluded_by_block_wax_false_when_not_blocked() -> None:
    assert rules.excluded_by_block_wax(new_cell_id(_CLOCK), {}) is False


def test_excluded_by_hive_stand_policy_true_when_disallowed() -> None:
    policy = PlacementPolicy(allow_hive_stand=False)
    assert rules.excluded_by_hive_stand_policy(True, policy) is True


def test_excluded_by_hive_stand_policy_false_for_a_non_hive_stand_cell() -> None:
    policy = PlacementPolicy(allow_hive_stand=False)
    assert rules.excluded_by_hive_stand_policy(False, policy) is False


def test_fits_os_true_when_needs_names_no_os() -> None:
    assert rules.fits_os(TaskNeeds(), make_capabilities(os=OsFamily.LINUX)) is True


def test_fits_os_false_on_mismatch() -> None:
    needs = TaskNeeds(os=OsFamily.WINDOWS)
    assert rules.fits_os(needs, make_capabilities(os=OsFamily.LINUX)) is False


def test_fits_network_scopes_true_when_every_scope_is_reachable() -> None:
    capabilities = make_capabilities(network_scopes=("a.example.com", "b.example.com"))
    needs = TaskNeeds(network_scopes=("a.example.com",))
    assert rules.fits_network_scopes(needs, capabilities) is True


def test_fits_network_scopes_false_when_a_scope_is_missing() -> None:
    capabilities = make_capabilities(network_scopes=("a.example.com",))
    needs = TaskNeeds(network_scopes=("b.example.com",))
    assert rules.fits_network_scopes(needs, capabilities) is False


def test_fits_exoskeleton_true_when_not_needed() -> None:
    capabilities = make_capabilities(has_display=False, can_start_display=False)
    assert rules.fits_exoskeleton(TaskNeeds(), capabilities) is True


def test_fits_exoskeleton_true_with_a_display() -> None:
    capabilities = make_capabilities(has_display=True)
    assert rules.fits_exoskeleton(TaskNeeds(exoskeleton=True), capabilities) is True


def test_fits_exoskeleton_false_with_no_display_and_cannot_start_one() -> None:
    capabilities = make_capabilities(has_display=False, can_start_display=False)
    assert rules.fits_exoskeleton(TaskNeeds(exoskeleton=True), capabilities) is False


def _real_candidate(*, has_free_capacity: bool) -> RealCandidate:
    return RealCandidate(
        warden_id=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        capabilities=make_capabilities(),
        comb_shield=CombShieldLevel.MEADOW,
        is_hive_stand=False,
        has_free_capacity=has_free_capacity,
    )


def test_real_has_forage_true_when_the_caller_measured_free_capacity() -> None:
    assert rules.real_has_forage(_real_candidate(has_free_capacity=True)) is True


def test_real_has_forage_false_when_the_caller_measured_none() -> None:
    assert rules.real_has_forage(_real_candidate(has_free_capacity=False)) is False


def test_virtual_has_headroom_true_for_none() -> None:
    assert rules.virtual_has_headroom(None) is True


def test_virtual_has_headroom_false_for_zero() -> None:
    assert rules.virtual_has_headroom(0) is False


def test_virtual_has_headroom_true_for_positive() -> None:
    assert rules.virtual_has_headroom(3) is True


def test_virtual_fits_os_true_when_needs_names_no_os() -> None:
    assert rules.virtual_fits_os(TaskNeeds(), _spec()) is True


def test_virtual_fits_os_false_on_mismatch() -> None:
    # base-ubuntu's own promised capacity reports LINUX (make_capacity's own default).
    assert rules.virtual_fits_os(TaskNeeds(os=OsFamily.WINDOWS), _spec()) is False


def test_virtual_fits_exoskeleton_false_when_the_spec_provisions_none() -> None:
    spec = _spec(exoskeleton=False)
    assert rules.virtual_fits_exoskeleton(TaskNeeds(exoskeleton=True), spec) is False


def test_virtual_fits_exoskeleton_true_when_the_spec_provisions_one() -> None:
    spec = _spec(exoskeleton=True)
    assert rules.virtual_fits_exoskeleton(TaskNeeds(exoskeleton=True), spec) is True


def test_virtual_fits_network_scopes_false_for_none_policy() -> None:
    spec = _spec(network_policy=NetworkPolicy.NONE)
    needs = TaskNeeds(network_scopes=("example.com",))
    assert rules.virtual_fits_network_scopes(needs, spec) is False


def test_virtual_fits_network_scopes_true_for_egress_only() -> None:
    spec = _spec(network_policy=NetworkPolicy.EGRESS_ONLY)
    needs = TaskNeeds(network_scopes=("example.com",))
    assert rules.virtual_fits_network_scopes(needs, spec) is True


def test_virtual_fits_network_scopes_checks_the_allowlist() -> None:
    spec = _spec(network_policy=NetworkPolicy.ALLOWLIST, network_allowlist=("example.com",))
    fits = TaskNeeds(network_scopes=("example.com",))
    misses = TaskNeeds(network_scopes=("other.example.com",))
    assert rules.virtual_fits_network_scopes(fits, spec) is True
    assert rules.virtual_fits_network_scopes(misses, spec) is False


def test_virtual_has_forage_true_when_the_spec_covers_the_footprint() -> None:
    assert rules.virtual_has_forage(_spec(), make_footprint()) is True


def test_virtual_has_forage_false_when_max_sub_bees_is_zero() -> None:
    spec = _spec(capacity=make_capacity(max_sub_bees=0))
    assert rules.virtual_has_forage(spec, make_footprint()) is False


def test_virtual_has_forage_false_when_cpu_is_insufficient() -> None:
    spec = _spec(cpu_cores=0.1)
    assert rules.virtual_has_forage(spec, make_footprint(cpu_cores=1.0)) is False


def test_caution_rank_one_when_cautioned() -> None:
    cell_id = new_cell_id(_CLOCK)
    cautioned = {cell_id: WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ00", text="reason")}
    assert rules.caution_rank(cell_id, cautioned) == 1


def test_caution_rank_zero_when_clean() -> None:
    assert rules.caution_rank(new_cell_id(_CLOCK), {}) == 0
