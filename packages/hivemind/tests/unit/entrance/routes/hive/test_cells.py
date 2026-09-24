"""Test hivemind.entrance.routes.hive.cells: every Cell with its tier, access, mode, lease and work.

Over real listeners and a real Queen placing a real goal: the Cell her attached Warden runs on is
listed with its Comb Shield tier, access level, Warden, the task placed on it, the lease its
newest lease edge left, the mode its Warden's newest mode edge left and a Mask state that is
always present (null until tracked); one Cell reads the same alone; an unknown Cell is a 404 with
its own code; and a submit-only device is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/cells.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.entrance.views import trail_event
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider

_OBSERVE = ProgramGrant(capabilities=("observe",))


async def test_a_cell_shows_its_tier_warden_work_lease_and_mode() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
        # What the Warden records on its own segment, as it reaches the Queen's trail.
        await rig.deps.trail.record(trail_event(rig, "cell.leased", link.cell.id))
        await rig.deps.trail.record(trail_event(rig, "warden.watch", link.warden_id))

        listed = await client.call(session, "GET", "/v1/cells")
        alone = await client.call(session, "GET", f"/v1/cells/{link.cell.id}")

    assert listed.status_code == alone.status_code == 200, listed.text
    [cell] = listed.json()["cells"]
    assert cell == alone.json()
    assert (cell["id"], cell["kind"], cell["warden_id"]) == (link.cell.id, "REAL", link.warden_id)
    assert (cell["comb_shield"], cell["access_level"]) == (
        link.cell.comb_shield.value,
        link.cell.access_level.value,
    )
    assert cell["current_tasks"] == [goal_id]
    assert (cell["lease_state"], cell["mode"], cell["last_event"]) == (
        "OPEN",
        "WATCH",
        "cell.leased",
    )
    assert "mask_state" in cell and cell["mask_state"] is None
    assert cell["virtual_status"] is None


async def test_an_unknown_cell_is_a_404_with_its_own_code() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)

        missing = await client.call(session, "GET", "/v1/cells/cell_0000000000000000000000000A")

    assert missing.status_code == 404
    assert missing.json()["error"] == "hivemind.entrance.cell_not_found"


async def test_a_submit_only_device_is_refused_the_cell_reads() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        listed = await client.call(session, "GET", "/v1/cells")

    assert listed.status_code == 403
