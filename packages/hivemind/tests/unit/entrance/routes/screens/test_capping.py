"""Test hivemind.entrance.routes.screens.capping: the Capping queue over a real listener.

Capping events on the Queen's trail fold into the queue; a device without ``observe`` is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/capping.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, serving
from builders.entrance.views import trail_event

from waggle.ids import new_cell_id, new_message_id, new_task_id


async def test_capping_events_fold_into_the_queue_and_its_verdicts() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("observe",)))
        proposal = new_message_id(rig.clock)
        payload = {
            "task_id": new_task_id(rig.clock),
            "cell_id": new_cell_id(rig.clock),
            "tier": "READ_ONLY",
        }
        await rig.deps.trail.record(trail_event(rig, "capping.proposed", proposal, payload))
        await rig.deps.trail.record(
            trail_event(
                rig, "capping.rejected", proposal, {"tier": "READ_ONLY", "failing_check": "SCOPE"}
            )
        )

        view = await client.call(session, "GET", "/v1/capping")

    assert view.status_code == 200, view.text
    [row] = view.json()["finished"]
    assert (row["id"], row["state"], row["failing_check"]) == (proposal, "REJECTED", "SCOPE")
    assert [v["verdict"] for v in view.json()["verdicts"]] == ["REJECTED"]
    assert view.json()["in_flight"] == []


async def test_a_submit_only_device_is_refused_the_capping_view() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        refused = await client.call(session, "GET", "/v1/capping")

    assert refused.status_code == 403
