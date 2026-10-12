"""Test hivemind.entrance.routes.screens.attendant: the Attendant views over a real listener.

The Queen's view and an attached Warden's both answer; a stray principal is a 422 and a well-formed
Warden id nobody attached is a 404 with its own code; a submit-only device is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/attendant.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider

_OBSERVE = ProgramGrant(capabilities=("observe",))
_STRANGER = "warden_0000000000000000000000000A"


async def test_the_queen_and_a_warden_each_have_a_view_of_the_task_graph() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)

        queen = await client.call(session, "GET", "/v1/attendant/queen")
        warden = await client.call(session, "GET", f"/v1/attendant/{link.warden_id}")

    assert queen.status_code == warden.status_code == 200, queen.text
    assert queen.json()["principal"] == "queen"
    assert [node["task_id"] for node in queen.json()["nodes"]] == [goal_id]
    assert queen.json()["inbox_is_reconstructed"] is True
    assert warden.json()["principal"] == link.warden_id


async def test_a_stray_principal_is_refused_by_shape_or_as_unknown() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        other, other_session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        malformed = await client.call(session, "GET", "/v1/attendant/bob")
        unknown = await client.call(session, "GET", f"/v1/attendant/{_STRANGER}")
        refused = await other.call(other_session, "GET", "/v1/attendant/queen")

    assert malformed.status_code == 422
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "hivemind.entrance.principal_not_found"
    assert refused.status_code == 403
