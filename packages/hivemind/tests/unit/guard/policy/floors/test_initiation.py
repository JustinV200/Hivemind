"""Tests for hivemind.guard.policy.floors.initiation: only a human's own request starts Night Veil.

Roadmap step 10.3c, through `evaluate`: the principal holds every tier, so any refusal here is the
floor's.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/floors/initiation.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.floors.initiation for the module under test.
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

_POLICY = load_guard_policy()
_NIGHT_VEIL = CombShieldLevel.NIGHT_VEIL
_HUMANS_ASK = GoalRequestFacts(origin=RequestOrigin.HUMAN, comb_shield=_NIGHT_VEIL)
_INITIATION = "guard.tier_floor.night_veil_initiation"


def _placement(
    context: PolicyContext,
    point: EnforcementPoint = EnforcementPoint.PLACEMENT,
    needed: str = "cell:comb_shield:night_veil",
) -> str:
    """Decide the Queen's check at `point` for a goal holding every tier; return the rule."""
    request = make_request(
        needed,
        "cell:virtual",
        "cell:comb_shield:*",
        kind=PrincipalKind.QUEEN,
        point=point,
        context=context,
    )
    return evaluate(request, _POLICY).rule


def _night_veil(**facts: object) -> PolicyContext:
    """A Night Veil task's context: requested tier NIGHT_VEIL, plus the facts under test."""
    return PolicyContext(bound_tier=_NIGHT_VEIL).model_copy(update=facts)


# ──────────────────────────────────────────────────────────────────────────────
# Initiation: only a human's own request names Night Veil
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("point", [EnforcementPoint.PLACEMENT, EnforcementPoint.COMB_SHIELD_EGRESS])
def test_a_humans_own_night_veil_request_passes_the_floor(point: EnforcementPoint) -> None:
    context = _night_veil(origin=RequestOrigin.HUMAN, goal_request=_HUMANS_ASK)

    assert _placement(context, point) == "guard.held"


def test_a_planner_that_set_night_veil_on_its_own_is_refused() -> None:
    # No durable request stands behind the task: a model chose the tier.
    decision_rule = _placement(_night_veil(origin=RequestOrigin.HUMAN, goal_request=None))

    assert decision_rule == _INITIATION


@pytest.mark.parametrize("origin", [RequestOrigin.QUEEN, RequestOrigin.WARDEN, None])
def test_a_task_the_queen_or_a_warden_asked_for_never_initiates_night_veil(
    origin: RequestOrigin | None,
) -> None:
    context = _night_veil(origin=origin, goal_request=_HUMANS_ASK)

    assert _placement(context) == _INITIATION


@pytest.mark.parametrize("asked", [None, CombShieldLevel.PROPOLIS])
def test_a_request_that_did_not_name_night_veil_does_not_initiate_it(
    asked: CombShieldLevel | None,
) -> None:
    facts = GoalRequestFacts(origin=RequestOrigin.HUMAN, comb_shield=asked)
    request = make_request(
        "cell:virtual",
        "cell:virtual",
        kind=PrincipalKind.QUEEN,
        point=EnforcementPoint.PLACEMENT,
        context=_night_veil(origin=RequestOrigin.HUMAN, goal_request=facts),
    )

    decision = evaluate(request, _POLICY)

    assert decision.rule == _INITIATION
    assert "its goal request asked for" in decision.reason


def test_the_initiation_floor_only_guards_where_night_veil_work_begins() -> None:
    # A grant for a task already placed was initiated at its placement, not here.
    context = _night_veil(origin=RequestOrigin.QUEEN)
    request = make_request(
        "llm:worker",
        "llm:*",
        kind=PrincipalKind.QUEEN,
        point=EnforcementPoint.GRANT_ISSUE,
        context=context.model_copy(update={"binding_local": True}),
    )

    assert evaluate(request, _POLICY).rule == "guard.held"


def test_initiation_is_untouched_for_any_other_tier() -> None:
    context = PolicyContext(bound_tier=CombShieldLevel.PROPOLIS, origin=RequestOrigin.QUEEN)

    assert _placement(context, needed="cell:comb_shield:propolis") == "guard.held"
