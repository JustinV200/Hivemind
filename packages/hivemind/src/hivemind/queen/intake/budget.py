"""Define goal_spend_cap and goal_budgets: a requested goal's own budget, applied to its grants.

A goal request may name a budget in US dollars (`GoalRequest.budget_usd`, docs/adr/0040): the
goal's own spend cap. Every task planned from the request carries it (`TaskSpec.spend_cap_usd`),
and the Queen applies it wherever she sizes a goal's spend from `[forage] spend_cap_per_goal_usd`
(`QueenDeps.budgets.spend_cap_usd`): a fresh grant's budgets (`hivemind.queen.dispatcher`), a
Warden's request for more spend (`hivemind.queen.forage.requests`) and the cost-cap Clustering
trigger (`hivemind.queen.cluster.triggers`). It is always the lower of the two, so a request can
tighten a goal's cap but never lift it past what the manifest allows.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Called by the three places named above. Calls into `hivemind.brood_chamber`
    (TaskSpec) and `hivemind.forage` (GoalBudgets) only; pure, no I/O.

Key invariants:
    - `goal_spend_cap(budgets, spec) <= budgets.spend_cap_usd`, always; a task with no cap of its
      own (every goal the operator submitted locally) gets exactly the manifest's.

See Also:
    - hivemind.forage.allocate for GoalBudgets and the grant arithmetic that reads it.
    - hivemind.brood_chamber.task.goal_request for GoalSpendCap, the field this reads.
"""

from __future__ import annotations

import dataclasses

from hivemind.brood_chamber import TaskSpec
from hivemind.forage import GoalBudgets

__all__ = ["goal_budgets", "goal_spend_cap"]


def goal_spend_cap(budgets: GoalBudgets, spec: TaskSpec) -> float:
    """Return the spend cap that applies to `spec`'s goal: the lower of its own and the manifest's.

    Args:
        budgets: The manifest's per-goal caps (`QueenDeps.budgets`).
        spec: Any task of the goal; every task of one goal carries the same cap.

    Returns:
        `budgets.spend_cap_usd`, or the goal's own cap when that is lower.
    """
    if spec.spend_cap_usd is None:
        return budgets.spend_cap_usd  # No budget of its own: the manifest's cap alone.
    return min(spec.spend_cap_usd, budgets.spend_cap_usd)


def goal_budgets(budgets: GoalBudgets, spec: TaskSpec) -> GoalBudgets:
    """Return `budgets` with its spend cap narrowed to `spec`'s goal's own, when it has one.

    Args:
        budgets: The manifest's per-goal caps (`QueenDeps.budgets`).
        spec: The task a fresh grant is being sized for.

    Returns:
        `budgets` unchanged for a goal with no budget of its own, else a copy with the lower cap.
    """
    cap = goal_spend_cap(budgets, spec)
    if cap == budgets.spend_cap_usd:
        return budgets
    return dataclasses.replace(budgets, spend_cap_usd=cap)
