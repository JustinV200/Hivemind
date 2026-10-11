"""Define choose_effort: how hard a model is asked to think, from a task's tempo.

Effort is a per-binding value, never a global (codingrules section 8.14): an urgent, low-stakes
task should get a quick answer from a fast model, and a critical one the model's full attention.
``choose_effort`` is the one rule that turns a task's tempo (how fast it must be done and how
right it must be) and the manifest row's own configured effort into the effort routing reports.
Accuracy outranks speed, because tempo never overrides safety and a wrong answer to a critical
task is not made right by being quick: a LOW accuracy bar always means LOW effort, a CRITICAL one
always HIGH, and only between those does a tight latency budget (under ``TIGHT_BUDGET_S``) pull
the effort down to LOW. Everything else keeps the effort the operator wrote on the row.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.llm.routing``. Called by
    ``hivemind.llm.routing.policy.choose_binding`` once per decision. Calls into
    ``hivemind.forage`` (``AccuracyBar``, ``Effort``, ``Tempo``) only.

Key invariants:
    - Pure and total: the same tempo and row effort always give the same effort.
    - Tempo can only move effort to the two extremes; it never invents MEDIUM, so a row that asks
      for MEDIUM or HIGH keeps it for every bar between LOW and CRITICAL unless the budget is tight.

See Also:
    - .claude/codingrules.md section 8.14 for "routing reads tempo".
    - hivemind.forage.slots for ``Effort``.
"""

from __future__ import annotations

from hivemind.forage import AccuracyBar, Effort, Tempo

__all__ = ["TIGHT_BUDGET_S", "choose_effort"]

# A latency budget under this many seconds is tight enough that a quick answer from a fast
# model matters more than a long think; it is roadmap step 8.1's own figure.
TIGHT_BUDGET_S = 10.0


def choose_effort(tempo: Tempo, row_effort: Effort) -> Effort:
    """Return the effort to ask the chosen model for.

    Args:
        tempo: The task's accuracy bar and optional latency budget.
        row_effort: The effort the chosen ``[llm.slots]`` row is configured with.

    Returns:
        ``LOW`` for a LOW accuracy bar; ``HIGH`` for a CRITICAL one; ``LOW`` for any other bar
        with a latency budget under ``TIGHT_BUDGET_S``; otherwise ``row_effort`` unchanged.
    """
    if tempo.accuracy is AccuracyBar.LOW:
        return Effort.LOW  # Urgent or not, the bar is low: the cheapest thinking will do.
    if tempo.accuracy is AccuracyBar.CRITICAL:
        return Effort.HIGH  # Must not be wrong: speed does not buy a worse answer.
    budget = tempo.latency_budget_s
    if budget is not None and budget < TIGHT_BUDGET_S:
        return Effort.LOW  # Little time to wait: the row's long think would blow the budget.
    return row_effort
