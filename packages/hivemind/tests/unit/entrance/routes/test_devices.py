"""Test hivemind.entrance.routes.devices: revoking a device settles all of its work in one step.

Over real listeners and a real Queen, through the loopback-only revoke route: a program has one
goal planned and placed on the Queen's Warden and a second one waiting, not yet planned. Revoking
it with ``cancel_goals`` refuses the waiting request (its device can no longer be told anything
it asked for is coming), stops the placed work on its Warden (a ``TaskCancel`` reaches the Warden
before the task is recorded CANCELLED) and names both in the answer and in the revocation's own
trail event. Without ``cancel_goals`` the placed goal is named as left running, the waiting
request refused all the same.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/devices.py (codingrules section 3); the revoke row.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen import GoalRequestState
from waggle.ids import TaskId

_WAIT_S = 5.0  # Generous: planning here is one scripted model call.
# A device's set is its goals' ceiling: running one on the Hive Stand needs its Cell, a tier and
# model access, besides submitting it.
_WORKER = ProgramGrant(
    capabilities=("observe", "entrance:submit", "cell:hive_stand", "cell:comb_shield:*", "llm:*")
)


@dataclass(frozen=True, slots=True)
class _Work:
    """A program's two goals: one planned and placed, one still waiting to be planned."""

    device_id: str
    placed_request: str
    goal_id: TaskId
    waiting_request: str


async def _work(rig: ServingRig) -> _Work:
    """Have a program get one goal planned and placed, then submit a second the Queen never sees."""
    client, session = await rig.program(_WORKER)
    running = asyncio.ensure_future(rig.queen.run())
    try:
        placed = await client.call(session, "POST", "/v1/goals", {"text": "Write a haiku."})
        request_id = placed.json()["id"]
        async with asyncio.timeout(_WAIT_S):
            while True:
                if (await rig.deps.goal_requests.get(request_id)).goal_id is not None:
                    break
                await asyncio.sleep(0.01)
        await rig.warden_end.wait_for_assignment()
    finally:
        # Stopped, so the second request stays RECEIVED until the revocation refuses it.
        await rig.queen.stop()
        await running
    waiting = await client.call(session, "POST", "/v1/goals", {"text": "Write another."})
    goal_id = (await rig.deps.goal_requests.get(request_id)).goal_id
    assert goal_id is not None
    return _Work(session.key.device_id, request_id, goal_id, waiting.json()["id"])


async def test_revoking_with_cancel_goals_refuses_waiting_work_and_stops_placed_work() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        work = await _work(rig)
        console, console_session = await rig.console_session()

        path = f"/v1/devices/{work.device_id}/revoke"
        revoked = await console.call(console_session, "POST", path, {"cancel_goals": True})
        cancel = await rig.warden_end.wait_for_task_cancel()
        waiting = await rig.deps.goal_requests.get(work.waiting_request)
        task = await rig.deps.chamber.get(work.goal_id)
        [event] = await rig.deps.trail.query(TrailQuery(kind="guard.entrance_revoked"))

    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["device"]["status"] == "REVOKED"
    assert body["requests_refused"] == [work.waiting_request]
    assert (body["goals_cancelled"], body["goals_left_running"]) == ([work.goal_id], [])
    assert cancel.task_id == work.goal_id
    assert waiting.state is GoalRequestState.REFUSED
    assert task.status is TaskStatus.CANCELLED
    assert event.payload["requests_refused"] == [work.waiting_request]
    assert event.payload["goals_cancelled"] == [work.goal_id]


async def test_revoking_without_cancel_goals_names_the_goal_left_running() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        work = await _work(rig)
        console, console_session = await rig.console_session()

        path = f"/v1/devices/{work.device_id}/revoke"
        revoked = await console.call(console_session, "POST", path, {"cancel_goals": False})
        waiting = await rig.deps.goal_requests.get(work.waiting_request)
        task = await rig.deps.chamber.get(work.goal_id)
        [event] = await rig.deps.trail.query(TrailQuery(kind="guard.entrance_revoked"))

    body = revoked.json()
    assert (body["goals_cancelled"], body["goals_left_running"]) == ([], [work.goal_id])
    assert body["requests_refused"] == [work.waiting_request]
    assert waiting.state is GoalRequestState.REFUSED
    assert task.status is not TaskStatus.CANCELLED
    assert event.payload["goals_left_running"] == [work.goal_id]
