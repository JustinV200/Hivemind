"""Tests for hivemind.llm.routing.effort: choose_effort, the tempo-to-effort rule.

Fits into the Hive:
    Mirrors src/hivemind/llm/routing/effort.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.routing.effort for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.forage import AccuracyBar, Effort, Tempo
from hivemind.llm.routing import TIGHT_BUDGET_S, choose_effort


@pytest.mark.parametrize("row_effort", list(Effort))
def test_choose_effort_is_low_for_a_low_accuracy_bar(row_effort: Effort) -> None:
    assert choose_effort(Tempo(accuracy=AccuracyBar.LOW), row_effort) is Effort.LOW


@pytest.mark.parametrize("row_effort", list(Effort))
def test_choose_effort_is_high_for_a_critical_accuracy_bar(row_effort: Effort) -> None:
    assert choose_effort(Tempo(accuracy=AccuracyBar.CRITICAL), row_effort) is Effort.HIGH


def test_choose_effort_keeps_critical_high_even_with_a_tight_budget() -> None:
    tempo = Tempo(accuracy=AccuracyBar.CRITICAL, latency_budget_s=1.0)

    assert choose_effort(tempo, Effort.MEDIUM) is Effort.HIGH


@pytest.mark.parametrize("bar", [AccuracyBar.NORMAL, AccuracyBar.HIGH])
def test_choose_effort_is_low_under_a_tight_latency_budget(bar: AccuracyBar) -> None:
    tempo = Tempo(accuracy=bar, latency_budget_s=TIGHT_BUDGET_S - 0.1)

    assert choose_effort(tempo, Effort.HIGH) is Effort.LOW


@pytest.mark.parametrize("budget", [None, TIGHT_BUDGET_S, 60.0])
def test_choose_effort_keeps_the_rows_own_effort_without_a_tight_budget(
    budget: float | None,
) -> None:
    tempo = Tempo(accuracy=AccuracyBar.NORMAL, latency_budget_s=budget)

    assert choose_effort(tempo, Effort.MEDIUM) is Effort.MEDIUM
