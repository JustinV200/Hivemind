"""Tests for hivemind.guard.policy.floors.night_veil: what a Night Veil task may never do.

Roadmap steps 10.3a and 10.3d, through `evaluate`: every refusal here holds while the principal
holds the capability, so it is the floor, never the held set, that decides.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/floors/night_veil.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.floors.night_veil for the module under test.
"""

from __future__ import annotations

import ipaddress

import pytest
from builders.guard import make_request

from hivemind.cell import CombShieldLevel
from hivemind.guard.policy import (
    ControlLink,
    EnforcementPoint,
    PolicyContext,
    PrincipalKind,
    evaluate,
)
from hivemind.guard.policy.defaults import load_guard_policy
from hivemind.guard.policy.floors import is_night_veil

_POLICY = load_guard_policy()
_NIGHT_VEIL = CombShieldLevel.NIGHT_VEIL
_ON_CELL = PolicyContext(comb_shield=_NIGHT_VEIL)  # An action on a Night Veil Cell.
_BOUND = PolicyContext(bound_tier=_NIGHT_VEIL)  # A task bound to, or asking for, Night Veil.
_ONION = "hivestand2a5bq3v7lx6hbhxg4hjk6bqjhm6mmuwq4z7s3fqhp2fy6s2ad.onion"
_TOR = "socks5h://127.0.0.1:9050"


def _decide(
    needed: str,
    *held: str,
    context: PolicyContext,
    kind: PrincipalKind = PrincipalKind.WORKER,
) -> str:
    """Decide `needed` for `kind` holding `held` (or `needed` itself); return the deciding rule."""
    request = make_request(needed, *(held or (needed,)), kind=kind, context=context)
    return evaluate(request, _POLICY).rule


def test_is_night_veil_reads_the_cells_tier_or_the_tasks() -> None:
    assert is_night_veil(_ON_CELL) and is_night_veil(_BOUND)
    assert not is_night_veil(PolicyContext(comb_shield=CombShieldLevel.PROPOLIS))
    assert not is_night_veil(PolicyContext())


# ──────────────────────────────────────────────────────────────────────────────
# Virtual only
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("needed", ["cell:hive_stand", "cell:real:cell_x"])
def test_night_veil_work_is_never_placed_on_a_real_cell_even_when_held(needed: str) -> None:
    rule = _decide(needed, "cell:hive_stand", "cell:real:*", context=_BOUND)

    assert rule == "guard.tier_floor.night_veil_virtual_only"


def test_a_virtual_cell_is_left_to_the_held_set_which_must_hold_it() -> None:
    assert _decide("cell:virtual", context=_BOUND) == "guard.held"
    assert _decide("cell:virtual", "cell:hive_stand", context=_BOUND) == "guard.not_held"


def test_real_placement_is_untouched_for_any_other_tier() -> None:
    context = PolicyContext(bound_tier=CombShieldLevel.PROPOLIS)

    assert _decide("cell:hive_stand", context=context) == "guard.held"


# ──────────────────────────────────────────────────────────────────────────────
# Local-only slots
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("point", [EnforcementPoint.SLOT_BINDING, EnforcementPoint.GRANT_ISSUE])
def test_a_night_veil_binding_to_a_hosted_model_is_refused(point: EnforcementPoint) -> None:
    context = _BOUND.model_copy(update={"binding_local": False})
    request = make_request("llm:worker", "llm:*", point=point, context=context)

    decision = evaluate(request, _POLICY)

    assert decision.rule == "guard.tier_floor.night_veil_local_slots"
    assert "this binding is hosted or served off the Cell" in decision.reason


def test_a_night_veil_binding_whose_locality_was_never_stated_fails_closed() -> None:
    decision = evaluate(make_request("llm:worker", "llm:*", context=_ON_CELL), _POLICY)

    assert decision.rule == "guard.tier_floor.night_veil_local_slots"
    assert "was not shown local" in decision.reason


def test_a_local_night_veil_binding_is_left_to_the_held_set() -> None:
    context = _ON_CELL.model_copy(update={"binding_local": True})

    assert _decide("llm:worker", "llm:worker", context=context) == "guard.held"


def test_a_hosted_binding_is_untouched_off_night_veil() -> None:
    context = PolicyContext(comb_shield=CombShieldLevel.MEADOW, binding_local=False)

    assert _decide("llm:worker", context=context) == "guard.held"


# ──────────────────────────────────────────────────────────────────────────────
# Honey clearance
# ──────────────────────────────────────────────────────────────────────────────


def test_c2_honey_is_refused_on_night_veil_even_when_held() -> None:
    rule = _decide("honey:clearance:c2", "honey:clearance:c2", context=_ON_CELL)

    assert rule == "guard.tier_floor.night_veil_clearance"


@pytest.mark.parametrize("needed", ["honey:clearance:c0", "honey:clearance:c1"])
def test_c0_and_c1_honey_are_left_to_the_held_set(needed: str) -> None:
    # The HONEY_ACCESS point stays pending until phase 7; the floor already answers through it.
    request = make_request(
        needed, "honey:clearance:c2", point=EnforcementPoint.HONEY_ACCESS, context=_ON_CELL
    )

    assert evaluate(request, _POLICY).rule == "guard.held"


# ──────────────────────────────────────────────────────────────────────────────
# Location blindness
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("needed", ["geo:city", "wifi:scan", "host:metadata"])
def test_every_location_family_is_refused_on_night_veil(needed: str) -> None:
    rule = _decide(needed, "geo:*", "wifi:scan", "host:metadata", context=_BOUND)

    assert rule == "guard.tier_floor.night_veil_location"


def test_a_metadata_endpoint_is_refused_by_name_and_by_resolved_address() -> None:
    resolved = _BOUND.model_copy(
        update={"resolved_addresses": (ipaddress.ip_address("169.254.169.254"),)}
    )
    # The Queen is no bee, so the Hive-state floor never answers first: the location floor does.
    queen = PrincipalKind.QUEEN

    assert _decide("net:metadata.google.internal", "net:*", context=_BOUND, kind=queen) == (
        "guard.tier_floor.night_veil_location"
    )
    assert _decide("net:innocent.example", "net:*", context=resolved, kind=queen) == (
        "guard.tier_floor.night_veil_location"
    )


def test_an_ordinary_host_is_left_to_the_held_set_on_night_veil() -> None:
    assert _decide("net:example.com", "net:*", context=_BOUND) == "guard.held"


def test_the_location_families_are_untouched_off_night_veil() -> None:
    assert _decide("geo:city", "geo:*", context=PolicyContext()) == "guard.held"


# ──────────────────────────────────────────────────────────────────────────────
# The control link
# ──────────────────────────────────────────────────────────────────────────────


def test_a_night_veil_link_to_a_hidden_service_through_loopback_tor_passes_the_floor() -> None:
    context = PolicyContext(
        bound_tier=_NIGHT_VEIL, control_link=ControlLink(host=_ONION, socks_proxy_url=_TOR)
    )
    request = make_request(
        "cell:virtual", "cell:virtual", kind=PrincipalKind.QUEEN, context=context
    )

    assert evaluate(request, _POLICY).rule == "guard.held"


@pytest.mark.parametrize(
    ("host", "proxy", "problem"),
    [
        ("hive.example.com", _TOR, "its link dials a clearnet host"),
        ("10.8.0.1", _TOR, "its link dials a clearnet host"),
        (_ONION, None, "its link dials directly, with no SOCKS proxy"),
        (_ONION, "http://127.0.0.1:9050", "its proxy is not a SOCKS proxy"),
        (_ONION, "socks5h://10.8.0.1:9050", "its SOCKS proxy is off the Cell's own loopback"),
    ],
)
def test_a_night_veil_link_that_could_leave_tor_is_refused(
    host: str, proxy: str | None, problem: str
) -> None:
    context = PolicyContext(
        bound_tier=_NIGHT_VEIL, control_link=ControlLink(host=host, socks_proxy_url=proxy)
    )
    request = make_request(
        "cell:virtual", "cell:virtual", kind=PrincipalKind.QUEEN, context=context
    )

    decision = evaluate(request, _POLICY)

    assert decision.rule == "guard.tier_floor.night_veil_control_link"
    assert problem in decision.reason


def test_a_clearnet_link_is_untouched_for_any_other_tier() -> None:
    context = PolicyContext(
        bound_tier=CombShieldLevel.MEADOW, control_link=ControlLink(host="127.0.0.1")
    )
    request = make_request(
        "cell:virtual", "cell:virtual", kind=PrincipalKind.QUEEN, context=context
    )

    assert evaluate(request, _POLICY).rule == "guard.held"
