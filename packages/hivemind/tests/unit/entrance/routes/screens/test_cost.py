"""Test hivemind.entrance.routes.screens.cost: the Cost view over a real listener.

Completed model calls on the Queen's trail sum per goal, slot and provider with the local saving
per slot; a device without ``observe`` is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/cost.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, serving
from builders.observation import make_llm_call

from waggle.ids import new_task_id


async def test_calls_sum_per_goal_slot_and_provider_with_the_local_saving() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("observe",)))
        goal = new_task_id(rig.clock)
        await rig.deps.trail.record(
            make_llm_call(slot="WORKER", provider="cloud", cost_usd=0.5, lane={"goal_id": goal})
        )
        await rig.deps.trail.record(
            make_llm_call(slot="WORKER", provider="cloud", cost_usd=0.25, lane={"goal_id": goal})
        )

        view = await client.call(session, "GET", "/v1/cost")

    assert view.status_code == 200, view.text
    body = view.json()
    assert body["total_usd"] == 0.75 and body["if_local_saving_usd"] == 0.75
    assert [(g["goal_id"], g["calls"]) for g in body["goals"]] == [(goal, 2)]
    assert [(p["provider"], p["calls"]) for p in body["providers"]] == [("cloud", 2)]
    assert [(s["slot"], s["if_local_saving_usd"]) for s in body["slots"]] == [("WORKER", 0.75)]


async def test_a_submit_only_device_is_refused_the_cost_view() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        refused = await client.call(session, "GET", "/v1/cost")

    assert refused.status_code == 403
