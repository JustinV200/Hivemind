"""Test hivemind.entrance.routes.screens.fleet: the Fleet list over a real listener and Queen.

The Cell her attached Warden runs on is a row with its tier, Mask state (always present, null until
tracked), headline status, the Warden's place and the task her goal placed there; the legend names
all three tiers; a submit-only device is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/fleet.py (codingrules section 3).

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

_OBSERVE = ProgramGrant(capabilities=("observe",))


async def test_a_cell_is_a_row_with_its_tier_mask_badges_and_work() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
        rig.telemetry.record(link.warden_id, heartbeat(), rig.clock.now())

        fleet = await client.call(session, "GET", "/v1/fleet")

    assert fleet.status_code == 200, fleet.text
    [row] = fleet.json()["rows"]
    assert (row["id"], row["kind"], row["warden_id"]) == (link.cell.id, "REAL", link.warden_id)
    assert (row["comb_shield"], row["status"], row["current_task_id"]) == (
        link.cell.comb_shield.value,
        "ACTIVE",
        goal_id,
    )
    assert "mask_state" in row and row["mask_state"] is None
    # The Queen wrote the Cell's plan when she placed work there; the rig's one source is hosted.
    assert row["models_from"] == "hosted"
    assert [entry["tier"] for entry in fleet.json()["legend"]] == [
        "MEADOW",
        "PROPOLIS",
        "NIGHT_VEIL",
    ]


async def test_a_submit_only_device_is_refused_the_fleet() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        refused = await client.call(session, "GET", "/v1/fleet")

    assert refused.status_code == 403
