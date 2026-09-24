"""Tests for hivemind.queen.ticks.intake under a revocation: a revoked device's work never runs.

A device revoked at the Hive Entrance has its unplanned goal requests refused by the Queen
(``hivemind.queen.chat.withdraw.refuse_unplanned``). Her intake must then never plan them, and a
goal whose plan lands after its request was refused, or was persisted before a crash, must be
stopped rather than dispatched.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/intake.py (codingrules section 3), split by feature.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import dataclasses

from builders.human import (
    RecordingHumanChannel,
    is_planning,
    make_goal_request,
    queen_responder,
    single_task_plan,
)
from builders.queen import make_queen_deps

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.llm import FakeLLMProvider
from hivemind.llm.models import LLMRequest
from hivemind.queen.chat import refuse_unplanned
from hivemind.queen.deps import PlanningLane, QueenDeps, WardenLink
from hivemind.queen.goal_submission import GoalTerms, plan_goal_graph
from hivemind.queen.intake import GoalRequest, GoalRequestState, hold, receive, start_planning
from hivemind.queen.ticks.intake import drain_goal_requests
from waggle.ids import DeviceId, new_device_id

_REPLY: dict[str, object] = {"action": "RECORD", "reason": "Nothing to say."}
_REASON = "Its device was revoked."


@dataclasses.dataclass
class _Rig:
    """One Queen's collaborators, her attached Warden, a device, and what her planner saw."""

    deps: QueenDeps
    link: WardenLink
    channel: RecordingHumanChannel
    seen: list[LLMRequest]
    device: DeviceId

    def plans(self) -> int:
        """How many planning calls the planner answered so far."""
        return sum(1 for request in self.seen if is_planning(request))

    async def revoke(self) -> tuple[str, ...]:
        """Refuse the device's unplanned requests, as its revocation does."""
        links = {self.link.warden_id: self.link}
        return await refuse_unplanned(self.deps, links, self.device, _REASON)

    async def submit(self, **overrides: object) -> GoalRequest:
        """Commit one of the device's goal requests."""
        request = make_goal_request(self.deps.clock, device_id=self.device, **overrides)
        return await receive(self.deps, request)


def _rig() -> _Rig:
    seen: list[LLMRequest] = []
    channel = RecordingHumanChannel()
    responder = queen_responder(_REPLY, seen, single_task_plan)
    deps, link, _end = make_queen_deps(
        fake_provider=FakeLLMProvider(responder=responder), human_channel=channel
    )
    return _Rig(deps, link, channel, seen, new_device_id(deps.clock))


async def _goal_statuses(rig: _Rig, request: GoalRequest) -> set[TaskStatus]:
    """The statuses of every task planned for ``request``."""
    tasks = await rig.deps.chamber.list(TaskFilter(goal_request_id=request.id))
    return {task.status for task in tasks}


async def test_a_revoked_devices_received_and_held_requests_are_never_planned() -> None:
    rig = _rig()
    waiting = await rig.submit()
    held = await hold(rig.deps, await rig.submit(needs_confirmation=True))

    refused = await rig.revoke()
    await drain_goal_requests(rig.deps, (rig.link,))

    assert set(refused) == {waiting.id, held.id}
    for request in (waiting, held):
        assert (await rig.deps.goal_requests.get(request.id)).state is GoalRequestState.REFUSED
    assert rig.plans() == 0
    assert rig.deps.planning.task is None


async def test_a_goal_that_lands_after_its_request_was_refused_is_stopped_not_planned() -> None:
    rig = _rig()
    request = await rig.submit()
    await drain_goal_requests(rig.deps, (rig.link,))
    plan = rig.deps.planning.task
    # The plan runs beside the tick; the device is revoked before it lands.
    assert plan is not None and not plan.done()

    refused = await rig.revoke()
    await asyncio.wait({plan})
    await drain_goal_requests(rig.deps, (rig.link,))

    settled = await rig.deps.goal_requests.get(request.id)
    assert refused == (request.id,)
    assert (settled.state, settled.goal_id) == (GoalRequestState.REFUSED, None)
    assert await _goal_statuses(rig, request) == {TaskStatus.CANCELLED}
    assert "goal_request_planned" not in rig.channel.names()
    assert rig.plans() == 1


async def test_a_request_refused_after_its_goal_was_persisted_has_that_goal_stopped() -> None:
    rig = _rig()
    request = await start_planning(rig.deps, await rig.submit())
    # A crash (or the plan landing) left the graph persisted, the row still PLANNING.
    terms = GoalTerms(clearance=request.clearance, goal_request_id=request.id)
    await plan_goal_graph(rig.deps, (rig.link,), request.text, terms)

    refused = await rig.revoke()
    restarted = dataclasses.replace(rig.deps, planning=PlanningLane(), wake=asyncio.Event())
    await drain_goal_requests(restarted, (rig.link,))

    assert refused == (request.id,)
    assert (await rig.deps.goal_requests.get(request.id)).state is GoalRequestState.REFUSED
    assert await _goal_statuses(rig, request) == {TaskStatus.CANCELLED}
    assert rig.plans() == 1  # The one plan before the revocation; never a second.
