"""Tests for hivemind.queen.placement.policy: PlacementPolicy and check_night_veil.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/policy.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.policy for the module under test.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the rules under test.
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel, Isolation, RequestOrigin, TaskNeeds
from hivemind.hive import NetworkPolicy
from hivemind.queen.placement.policy import (
    NightVeilConstraints,
    NightVeilHostingView,
    PlacementPolicy,
    check_night_veil,
)

# TaskNeeds' own validator requires isolation=REQUIRED whenever comb_shield=NIGHT_VEIL.
_NIGHT_VEIL_NEEDS = TaskNeeds(comb_shield=CombShieldLevel.NIGHT_VEIL, isolation=Isolation.REQUIRED)


def _constraints(**overrides: object) -> NightVeilConstraints:
    """A fully-configured Night Veil profile, with any field replaced by `overrides`."""
    fields: dict[str, object] = {
        "required_network_policy": NetworkPolicy.VPN_TOR,
        "hive_stand_onion_address": "abc123.onion",
        "socks_proxy_url": "socks5h://127.0.0.1:9050",
        "locale_profile": "C.UTF-8",
    }
    fields.update(overrides)
    return NightVeilConstraints(**fields)  # type: ignore[arg-type]


def test_non_night_veil_needs_are_never_a_violation() -> None:
    # A completely unconfigured policy would fail every NIGHT_VEIL check; MEADOW must not care.
    violations = check_night_veil(
        TaskNeeds(), RequestOrigin.QUEEN, NightVeilHostingView(all_local=False), PlacementPolicy()
    )

    assert violations == ()


def test_fully_configured_human_request_has_no_violations() -> None:
    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.HUMAN,
        NightVeilHostingView(),
        PlacementPolicy(night_veil=_constraints()),
    )

    assert violations == ()


def test_a_non_human_origin_is_a_violation() -> None:
    for origin in (RequestOrigin.QUEEN, RequestOrigin.WARDEN):
        violations = check_night_veil(
            _NIGHT_VEIL_NEEDS,
            origin,
            NightVeilHostingView(),
            PlacementPolicy(night_veil=_constraints()),
        )
        assert any("human-originated" in v for v in violations)


def test_no_configured_profile_is_a_violation() -> None:
    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS, RequestOrigin.HUMAN, NightVeilHostingView(), PlacementPolicy()
    )

    assert any("tier profile" in v for v in violations)


def test_a_profile_with_the_wrong_network_policy_is_a_violation() -> None:
    bad = _constraints(required_network_policy=NetworkPolicy.EGRESS_ONLY)

    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.HUMAN,
        NightVeilHostingView(),
        PlacementPolicy(night_veil=bad),
    )

    assert any("VPN_TOR" in v for v in violations)


def test_a_profile_with_no_onion_address_is_a_violation() -> None:
    bad = _constraints(hive_stand_onion_address="")

    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.HUMAN,
        NightVeilHostingView(),
        PlacementPolicy(night_veil=bad),
    )

    assert any("hidden-service address" in v for v in violations)


def test_a_profile_with_no_socks_proxy_is_a_violation() -> None:
    bad = _constraints(socks_proxy_url="")

    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.HUMAN,
        NightVeilHostingView(),
        PlacementPolicy(night_veil=bad),
    )

    assert any("SOCKS proxy" in v for v in violations)


def test_a_profile_with_no_locale_is_a_violation() -> None:
    bad = _constraints(locale_profile="")

    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.HUMAN,
        NightVeilHostingView(),
        PlacementPolicy(night_veil=bad),
    )

    assert any("locale profile" in v for v in violations)


def test_a_non_local_hosting_view_is_a_violation_naming_the_slot() -> None:
    non_local = NightVeilHostingView(all_local=False, non_local_slots=("slot WORKER: hosted",))

    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.HUMAN,
        non_local,
        PlacementPolicy(night_veil=_constraints()),
    )

    assert any("resolve locally" in v and "slot WORKER" in v for v in violations)


def test_every_violation_kind_can_appear_together() -> None:
    violations = check_night_veil(
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.WARDEN,
        NightVeilHostingView(all_local=False, non_local_slots=("slot WORKER: hosted",)),
        PlacementPolicy(night_veil=None),
    )

    # origin + missing profile + non-local hosting: three violations, none short-circuited.
    assert len(violations) == 3


def test_check_night_veil_is_deterministic() -> None:
    args = (
        _NIGHT_VEIL_NEEDS,
        RequestOrigin.HUMAN,
        NightVeilHostingView(),
        PlacementPolicy(night_veil=_constraints()),
    )

    assert check_night_veil(*args) == check_night_veil(*args) == ()
