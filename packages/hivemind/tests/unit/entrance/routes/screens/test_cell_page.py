"""Test hivemind.entrance.routes.screens.cell_page: one Cell's page over a real listener.

The page for the Cell her Warden runs on carries the Cell, its Warden, the bees its Heartbeat
listed and the tasks placed there; an unknown Cell is a 404 with its own code; a submit-only device
is refused. The older ``/v1/cells/{id}`` read is unchanged beside it.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/cell_page.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.screens import heartbeat
from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from waggle.messages.supervision import WorkerState

_OBSERVE = ProgramGrant(capabilities=("observe",))


async def test_the_page_carries_the_cell_its_warden_bees_and_tasks() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
        rig.telemetry.record(
            link.warden_id, heartbeat([(goal_id, WorkerState.RUNNING)]), rig.clock.now()
        )

        page = await client.call(session, "GET", f"/v1/cells/{link.cell.id}/page")
        plain = await client.call(session, "GET", f"/v1/cells/{link.cell.id}")

    assert page.status_code == plain.status_code == 200, page.text
    body = page.json()
    assert body["cell"] == plain.json()
    assert (body["warden"]["id"], body["status"]) == (link.warden_id, "ACTIVE")
    assert [bee["role"] for bee in body["bees"]] == ["WARDEN", "DRONE"]
    assert [task["id"] for task in body["tasks"]] == [goal_id]
    assert body["last_patrol_at"] is None and body["exoskeleton_attached"] is None
    assert body["honey_folder"] == f"/cells/{link.cell.id}"


async def test_an_unknown_cell_is_a_404_and_a_submit_only_device_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        other, other_session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        missing = await client.call(
            session, "GET", "/v1/cells/cell_0000000000000000000000000A/page"
        )
        refused = await other.call(
            other_session, "GET", "/v1/cells/cell_0000000000000000000000000A/page"
        )

    assert missing.status_code == 404
    assert missing.json()["error"] == "hivemind.entrance.cell_not_found"
    assert refused.status_code == 403
