"""Test hivemind.entrance.reads.screens.cost: the Cost view read from the trail's llm.call events.

A Hive that made no call costs nothing. Completed calls sum per goal, slot and provider, with a
call attributed through its live grant's task's goal when its lane named only the grant.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/cost.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.screens import scene
from builders.forage import make_grant
from builders.observation import make_llm_call

from hivemind.entrance.reads.screens import read_cost
from hivemind.entrance.reads.screens.cost import grant_goals
from waggle.clock import FakeClock


async def test_a_hive_that_made_no_call_costs_nothing() -> None:
    clock = FakeClock()

    view = await read_cost(scene(clock).reads, clock.now())

    assert (view.total_usd, view.goals, view.slots, view.providers) == (0.0, [], [], [])


async def test_calls_sum_per_goal_and_a_grant_names_its_tasks_goal() -> None:
    clock = FakeClock()
    hive = scene(clock)
    [goal, step] = await hive.submit({"goal": (), "step": ("goal",)})
    grant = make_grant(clock=clock, task_id=step.id)
    await hive.deps.ledger.record_grant(grant)
    await hive.record(make_llm_call(slot="WORKER", cost_usd=0.5, lane={"goal_id": goal.id}))
    await hive.record(make_llm_call(slot="WORKER", cost_usd=0.25, lane={"grant_id": grant.id}))

    view = await read_cost(hive.reads, clock.now())

    assert await grant_goals(hive.reads) == {grant.id: goal.goal_id}
    assert [(g.goal_id, g.cost_usd, g.calls) for g in view.goals] == [(goal.id, 0.75, 2)]
    assert [(s.slot, s.if_local_saving_usd) for s in view.slots] == [("WORKER", 0.75)]
