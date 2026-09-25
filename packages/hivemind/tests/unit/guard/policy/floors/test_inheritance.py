"""Tests for hivemind.guard.policy.floors.inheritance: never a weaker egress than the bound tier.

Roadmap step 10.3b, through `evaluate`: the Queen holds every tier for the goal, so any refusal
here is the floor's.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/floors/inheritance.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.floors.inheritance for the module under test.
"""

from __future__ import annotations

import pytest
from builders.guard import make_request

from hivemind.cell import CombShieldLevel, RequestOrigin
from hivemind.guard.policy import (
    EnforcementPoint,
    GoalRequestFacts,
    PolicyContext,
    PrincipalKind,
    evaluate,
)
from hivemind.guard.policy.defaults import load_guard_policy
from hivemind.guard.policy.floors import tier_rank

_POLICY = load_guard_policy()
_INHERITANCE = "guard.tier_floor.tier_inheritance"
# A human's own request naming Night Veil, so the initiation floor never answers before this one.
_HUMANS_ASK = GoalRequestFacts(origin=RequestOrigin.HUMAN, comb_shield=CombShieldLevel.NIGHT_VEIL)


def _egress(bound: CombShieldLevel | None, activating: str, point: EnforcementPoint) -> str:
    """Decide activating `activating`'s egress for a task bound to `bound`; return the rule."""
    request = make_request(
        f"cell:comb_shield:{activating}",
        "cell:comb_shield:*",
        kind=PrincipalKind.QUEEN,
        point=point,
        context=PolicyContext(
            bound_tier=bound, origin=RequestOrigin.HUMAN, goal_request=_HUMANS_ASK
        ),
    )
    return evaluate(request, _POLICY).rule


def test_tier_rank_follows_declaration_order() -> None:
    ranks = [tier_rank(level) for level in CombShieldLevel]

    assert ranks == sorted(ranks) == [0, 1, 2]


@pytest.mark.parametrize(
    ("bound", "activating"),
    [
        (CombShieldLevel.PROPOLIS, "meadow"),
        (CombShieldLevel.NIGHT_VEIL, "propolis"),
        (CombShieldLevel.NIGHT_VEIL, "meadow"),
    ],
)
def test_egress_weaker_than_the_bound_tier_is_refused(
    bound: CombShieldLevel, activating: str
) -> None:
    rule = _egress(bound, activating, EnforcementPoint.COMB_SHIELD_EGRESS)

    assert rule == _INHERITANCE


@pytest.mark.parametrize("activating", ["propolis", "night_veil"])
def test_egress_at_or_above_the_bound_tier_is_left_to_the_held_set(activating: str) -> None:
    assert _egress(CombShieldLevel.PROPOLIS, activating, EnforcementPoint.COMB_SHIELD_EGRESS) == (
        "guard.held"
    )


def test_inheritance_is_judged_only_at_egress_and_only_with_a_bound_tier() -> None:
    assert _egress(CombShieldLevel.PROPOLIS, "meadow", EnforcementPoint.PLACEMENT) == "guard.held"
    assert _egress(None, "meadow", EnforcementPoint.COMB_SHIELD_EGRESS) == "guard.held"


def test_the_wildcard_tier_names_no_one_tier_and_is_left_to_the_held_set() -> None:
    assert _egress(CombShieldLevel.NIGHT_VEIL, "*", EnforcementPoint.COMB_SHIELD_EGRESS) == (
        "guard.held"
    )
