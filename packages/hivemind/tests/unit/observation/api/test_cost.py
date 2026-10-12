"""Test hivemind.observation.api.cost: spend per goal, slot and provider, and the local saving.

An empty trail costs nothing. Calls sum into one line per goal, slot and provider; a call is
attributed through its own goal id, else its grant's goal, else to a null goal; per-goal, per-slot
and per-provider totals each equal the grand total; and the saving per slot is its hosted spend
(calls that cost nothing save nothing).

Fits into the Hive:
    Mirrors src/hivemind/observation/api/cost.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.observation import T0, make_event, make_llm_call

from hivemind.observation import cost_lines, cost_view
from waggle.clock import FakeClock
from waggle.ids import new_grant_id, new_task_id

_CLOCK = FakeClock()


def test_an_empty_trail_costs_nothing() -> None:
    view = cost_view([], {}, T0)

    assert (view.total_usd, view.if_local_saving_usd) == (0.0, 0.0)
    assert (view.goals, view.slots, view.providers) == ([], [], [])


def test_calls_sum_per_goal_slot_and_provider_with_each_total_agreeing() -> None:
    goal_a, goal_b = new_task_id(_CLOCK), new_task_id(_CLOCK)
    grant = new_grant_id(_CLOCK)
    events = [
        make_llm_call(slot="WORKER", provider="cloud", cost_usd=0.5, lane={"goal_id": goal_a}),
        make_llm_call(slot="WORKER", provider="cloud", cost_usd=0.25, lane={"goal_id": goal_a}),
        make_llm_call(slot="WORKER", provider="local", cost_usd=0.0, lane={"goal_id": goal_a}),
        make_llm_call(slot="JUDGE", provider="cloud", cost_usd=1.0, lane={"grant_id": grant}),
        make_llm_call(slot="JUDGE", provider="cloud", cost_usd=2.0),  # Attributable to nothing.
        make_event("llm.spill", new_task_id(_CLOCK)),  # Not a call.
    ]

    view = cost_view(events, {grant: goal_b}, T0)

    assert view.total_usd == pytest.approx(3.75)
    assert [(g.goal_id, g.cost_usd, g.calls) for g in view.goals] == [
        (None, 2.0, 1),
        (goal_b, 1.0, 1),
        (goal_a, 0.75, 3),
    ]
    [worker_line] = [line for line in view.goals[2].lines if line.provider == "cloud"]
    assert (worker_line.slot, worker_line.calls, worker_line.input_tokens) == ("WORKER", 2, 200)
    assert sum(g.cost_usd for g in view.goals) == pytest.approx(view.total_usd)
    assert sum(s.cost_usd for s in view.slots) == pytest.approx(view.total_usd)
    assert sum(p.cost_usd for p in view.providers) == pytest.approx(view.total_usd)
    assert [(p.provider, p.calls) for p in view.providers] == [("cloud", 4), ("local", 1)]


def test_the_local_saving_is_the_hosted_spend_per_slot() -> None:
    events = [
        make_llm_call(slot="WORKER", provider="cloud", cost_usd=0.75),
        make_llm_call(slot="WORKER", provider="local", cost_usd=0.0),
        make_llm_call(slot="JUDGE", provider="local", cost_usd=0.0),
    ]

    view = cost_view(events, {}, T0)

    savings = {s.slot: (s.cost_usd, s.hosted_usd, s.if_local_saving_usd) for s in view.slots}
    assert savings == {"JUDGE": (0.0, 0.0, 0.0), "WORKER": (0.75, 0.75, 0.75)}
    assert view.if_local_saving_usd == 0.75


def test_lines_are_sorted_with_unattributed_calls_last() -> None:
    goal = new_task_id(_CLOCK)
    lines = cost_lines([make_llm_call(), make_llm_call(slot="WORKER", lane={"goal_id": goal})], {})

    assert [line.goal_id for line in lines] == [goal, None]
