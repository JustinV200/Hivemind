"""Test hivemind.entrance.routes.hive.forage: the Queen's Forage ledger as it stands.

Over real listeners and a real Queen: once a Cell's capacity is on her ledger and she has placed a
goal's task (issuing its grant), the read shows the capacity by Cell, the live grant with its
holder and task, the headroom the ledger computes and the Royal Reserve; a submit-only device is
refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/forage.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.forage import make_capacity
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider

_OBSERVE = ProgramGrant(capabilities=("observe",))


async def test_the_ledger_shows_capacity_live_grants_headroom_and_the_reserve() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        capacity = make_capacity()
        await rig.deps.ledger.report_capacity(link.cell.id, capacity)
        goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
        issued = await rig.warden_end.wait_for_grant()

        read = await client.call(session, "GET", "/v1/forage")
        ledger = rig.deps.ledger

    assert read.status_code == 200, read.text
    body = read.json()
    [reported] = body["capacities"]
    assert (reported["cell_id"], reported["cores"]) == (link.cell.id, capacity.host.cores)
    assert reported["max_sub_bees"] == capacity.max_sub_bees
    [grant] = body["grants"]
    assert (grant["id"], grant["holder"], grant["task_id"]) == (
        issued.grant_id,
        link.warden_id,
        goal_id,
    )
    headroom = ledger.headroom()
    assert body["headroom"] == {
        "sub_bees": headroom.sub_bees,
        "shared_seats": headroom.shared_seats,
    }
    assert body["reserve"]["seats"] == ledger.reserve.seats


async def test_a_submit_only_device_is_refused_the_forage_read() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        read = await client.call(session, "GET", "/v1/forage")

    assert read.status_code == 403
