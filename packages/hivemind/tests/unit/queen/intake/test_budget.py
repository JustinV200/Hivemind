"""Tests for hivemind.queen.intake.budget: a goal's own budget only ever lowers the cap.

Fits into the Hive:
    Mirrors src/hivemind/queen/intake/budget.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.intake.budget for goal_spend_cap and goal_budgets.
"""

from __future__ import annotations

from builders.tasks import make_task_spec

from hivemind.brood_chamber import TaskSpec
from hivemind.forage import GoalBudgets
from hivemind.queen.intake import goal_budgets, goal_spend_cap

_BUDGETS = GoalBudgets(spend_cap_usd=5.0, token_budget=1_000, max_sub_bees=2)


def _spec(cap: float | None) -> TaskSpec:
    return make_task_spec(spend_cap_usd=cap)


def test_a_goal_with_no_budget_of_its_own_gets_the_manifests_cap() -> None:
    assert goal_spend_cap(_BUDGETS, _spec(None)) == 5.0
    assert goal_budgets(_BUDGETS, _spec(None)) is _BUDGETS


def test_a_lower_budget_narrows_the_cap() -> None:
    assert goal_spend_cap(_BUDGETS, _spec(1.5)) == 1.5
    assert goal_budgets(_BUDGETS, _spec(1.5)).spend_cap_usd == 1.5
    assert goal_budgets(_BUDGETS, _spec(1.5)).max_sub_bees == 2  # Nothing else moves.


def test_a_higher_budget_never_lifts_the_cap() -> None:
    assert goal_spend_cap(_BUDGETS, _spec(50.0)) == 5.0
