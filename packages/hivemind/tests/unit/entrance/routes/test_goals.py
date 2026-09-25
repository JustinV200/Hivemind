"""Test hivemind.entrance.routes.goals: a goal is durable before its 202, and spends are guarded.

ADR-0040 and ADR-0041 over real listeners: ``POST /v1/goals`` answers ``202`` only once the goal
request is committed in the Queen's own table; a budget above ``step_up_spend`` needs a step-up; a
program (which no person types at) that would pass its daily cap has its goal held as a pending
confirmation, and a person's confirmation submits it exactly once, under the id minted when it was
held.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import (
    RIG_SECTION,
    ProgramGrant,
    RigOptions,
    ServingRig,
    serving,
)

from hivemind.queen import GoalRequest, GoalRequestQuery, GoalRequestState
from waggle.ids import DeviceId

_GOAL = {"text": "Tidy the garden notes into one page."}


async def test_a_goal_is_committed_in_the_queens_table_when_the_202_answers() -> None:
    async with serving() as rig:
        client, session = await rig.program()

        accepted = await client.call(session, "POST", "/v1/goals", _GOAL)
        request = await rig.deps.goal_requests.get(accepted.json()["id"])
        read = await client.call(session, "GET", f"/v1/goals/{request.id}")

    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["state"] == GoalRequestState.RECEIVED.value
    assert (request.text, request.device_id) == (_GOAL["text"], session.key.device_id)
    assert set(request.capabilities or ()) == set(ProgramGrant().capabilities)
    assert read.status_code == 200 and "text" not in read.json()


async def test_another_devices_goal_reads_as_not_found() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        other, other_session = await rig.program()
        accepted = await client.call(session, "POST", "/v1/goals", _GOAL)

        read = await other.call(other_session, "GET", f"/v1/goals/{accepted.json()['id']}")

    assert read.status_code == 404


async def test_a_budget_above_step_up_spend_needs_a_step_up_first() -> None:
    section = RIG_SECTION.model_copy(update={"step_up_spend": 1.0})
    async with serving(RigOptions(section=section)) as rig:
        client, session = await rig.program(ProgramGrant(interactive=True))
        costly = {**_GOAL, "budget_usd": 1.5}

        refused = await client.call(session, "POST", "/v1/goals", costly)
        await client.step_up(session)
        accepted = await client.call(session, "POST", "/v1/goals", costly)

    assert refused.status_code == 403
    assert refused.json()["error"].endswith("step_up_required")
    assert refused.json()["reason"] == "over_step_up_spend"
    assert refused.json()["pending_id"] is None
    assert accepted.status_code == 202, accepted.text


async def test_a_programs_goal_over_its_cap_is_held_then_submitted_once_on_confirmation() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(spend_cap_usd_per_day=1.0))
        console, console_session = await rig.console_session()
        await console.step_up(console_session)

        held = await client.call(session, "POST", "/v1/goals", {**_GOAL, "budget_usd": 1.5})
        pending_id = held.json()["pending_id"]
        before = await _requests_of(rig, session.key.device_id)
        path = f"/v1/entrance/confirmations/{pending_id}/confirm"
        confirmed = await console.call(console_session, "POST", path)
        again = await console.call(console_session, "POST", path)
        after = await _requests_of(rig, session.key.device_id)

    assert held.status_code == 403
    assert held.json()["reason"] == "over_daily_cap"
    assert pending_id is not None and before == []
    assert confirmed.status_code == 200, confirmed.text
    assert [request.id for request in after] == [confirmed.json()["goal_request_id"]]
    assert again.status_code == 403  # Settled: a second yes carries nothing out.
    assert again.json()["error"] == "hivemind.entrance.confirmation_refused"


async def _requests_of(rig: ServingRig, device_id: str) -> list[GoalRequest]:
    """Every goal request the device submitted, oldest first."""
    query = GoalRequestQuery(device_id=DeviceId(device_id), limit=10)
    return list(await rig.deps.goal_requests.list_requests(query))
