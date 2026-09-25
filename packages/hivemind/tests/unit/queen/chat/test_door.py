"""Test hivemind.queen.chat.door: the Hive Entrance's newest ways in, through the Queen.

``escalate_to_human`` puts an Alarm the Hive itself raised (the Entrance's remote listener failed)
in front of the human as an escalated Alarm does: in her inbox, on the trail, in the chat and on
every device. A revocation's two: ``cancel_goal`` stops a goal, placed work first on its Warden
with a TaskCancel, and says whether anything is left running; ``refuse_device_requests`` refuses
every request of the revoked device that is not planned yet, and a stale copy of one can no longer
move it afterwards.

Fits into the Hive:
    Mirrors src/hivemind/queen/chat/door.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.human import RecordingHumanChannel, make_goal_request
from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.attach import detach_warden
from hivemind.queen.chat import CANCEL_GRACE_S, ECHO_PREFIX, REVOKED_CODE, ChatKind, ChatQuery
from hivemind.queen.intake import (
    GoalRequestState,
    GoalSource,
    InvalidGoalRequestTransitionError,
    hold,
    receive,
    start_planning,
)
from hivemind.queen.queen import Queen
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState
from waggle.ids import new_alarm_id, new_device_id
from waggle.messages.supervision import AlarmContext

_REFUSED = "queen.goal_request_refused"  # The edge a revocation's refusal records.


def _two_step_plan(goal: str) -> dict[str, object]:
    """Two tasks, the second waiting on the first, so only the first can ever be dispatched."""
    acceptance: list[dict[str, object]] = [
        {"kind": "FILE_EXISTS", "subject": "done.txt", "argv": [], "expected": None}
    ]
    task = {"acceptance": acceptance, "needs": {}, "clearance": "C1"}
    return {
        "tasks": [
            {**task, "key": "draft", "title": "Draft", "objective": goal, "depends_on": []},
            {
                **task,
                "key": "polish",
                "title": "Polish",
                "objective": goal,
                "depends_on": ["draft"],
            },
        ]
    }


async def test_escalate_to_human_reaches_the_inbox_the_trail_the_chat_and_every_device() -> None:
    channel = RecordingHumanChannel()
    deps, _link, _warden_end = make_queen_deps(human_channel=channel)
    queen = Queen(deps)
    alarm = Alarm(
        id=new_alarm_id(deps.clock),
        kind=AlarmKind.OTHER,
        severity=AlarmSeverity.WARNING,
        origin=deps.identity.hive_id,
        attempts=0,
        context=AlarmContext(
            task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail="The Hive Entrance's remote listener failed (OSError).",
        clearance=HoneyClearance.C1,
        raised_at=deps.clock.now(),
        state=AlarmState.HANDLING,
    )

    await queen.escalate_to_human(alarm)

    [line] = [entry for entry in await deps.chat.read(ChatQuery()) if entry.kind is ChatKind.ALARM]
    assert line.ref == alarm.id
    assert [waiting.id for waiting in queen.human_inbox.alarms] == [alarm.id]
    assert await deps.trail.query(TrailQuery(kind="alarm.escalated"))
    assert channel.names() == ["alarm_raised"]


async def test_cancel_goal_stops_placed_work_on_its_warden_and_cancels_the_rest() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_two_step_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    assignment = await warden_end.wait_for_assignment()

    ended = await queen.cancel_goal(goal_id, "its device was revoked")
    order = await warden_end.wait_for_task_cancel()

    statuses = {
        task.spec.title: task.status
        for task in await deps.chamber.list(TaskFilter(goal_id=goal_id))
    }
    assert ended is True
    assert statuses == {"Draft": TaskStatus.CANCELLED, "Polish": TaskStatus.CANCELLED}
    assert order.task_id == assignment.task_id
    assert order.reason == "its device was revoked" and order.grace_s == CANCEL_GRACE_S
    await warden_end.close()


async def test_cancel_goal_leaves_work_on_an_unattached_warden_running() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_two_step_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    # Its Warden is gone: nothing can reach the work it runs, so it is not recorded cancelled.
    await detach_warden(queen, link.warden_id)

    ended = await queen.cancel_goal(goal_id, "its device was revoked")

    statuses = {
        task.spec.title: task.status
        for task in await deps.chamber.list(TaskFilter(goal_id=goal_id))
    }
    assert ended is False
    assert statuses["Polish"] is TaskStatus.CANCELLED
    assert statuses["Draft"] is not TaskStatus.CANCELLED
    await warden_end.close()


async def test_refuse_device_requests_refuses_only_that_devices_unplanned_requests() -> None:
    channel = RecordingHumanChannel()
    deps, _link, _warden_end = make_queen_deps(human_channel=channel)
    queen = Queen(deps)
    device, other = new_device_id(deps.clock), new_device_id(deps.clock)
    received = await receive(deps, make_goal_request(deps.clock, device_id=device))
    held = await hold(deps, await receive(deps, make_goal_request(deps.clock, device_id=device)))
    planning = await start_planning(
        deps, await receive(deps, make_goal_request(deps.clock, device_id=device))
    )
    others = await receive(deps, make_goal_request(deps.clock, device_id=other))

    refused = await queen.refuse_device_requests(device, "Its device was revoked.")

    states = {
        request.id: (await deps.goal_requests.get(request.id)).state
        for request in (received, held, planning, others)
    }
    assert set(refused) == {received.id, held.id, planning.id}
    assert states[others.id] is GoalRequestState.RECEIVED
    assert {states[request_id] for request_id in refused} == {GoalRequestState.REFUSED}
    assert channel.names().count("goal_request_refused") == 3
    codes = [e.payload["reason_code"] for e in await deps.trail.query(TrailQuery(kind=_REFUSED))]
    assert codes == [REVOKED_CODE] * 3


async def test_an_edge_from_a_stale_copy_fails_instead_of_overwriting_a_refusal() -> None:
    deps, _link, _warden_end = make_queen_deps()
    queen = Queen(deps)
    device = new_device_id(deps.clock)
    request = await receive(deps, make_goal_request(deps.clock, device_id=device))
    await queen.refuse_device_requests(device, "Its device was revoked.")

    with pytest.raises(InvalidGoalRequestTransitionError):
        await start_planning(deps, request)

    assert (await deps.goal_requests.get(request.id)).state is GoalRequestState.REFUSED


async def test_request_echoed_goal_is_held_and_echoed_before_it_returns_and_wakes_nobody() -> None:
    channel = RecordingHumanChannel()
    deps, _link, _warden_end = make_queen_deps(human_channel=channel)
    queen = Queen(deps)
    spoken = make_goal_request(deps.clock, source=GoalSource.SPOKEN, needs_confirmation=True)
    deps.wake.clear()  # Starts set (her first tick runs at once); cleared to see who sets it.

    held = await queen.request_echoed_goal(spoken)

    assert held.state is GoalRequestState.AWAITING_CONFIRMATION
    assert (await deps.goal_requests.get(spoken.id)).state is held.state
    [echo] = await deps.chat.read(ChatQuery())
    assert echo.text == f"{ECHO_PREFIX}{spoken.text}" and echo.ref == spoken.id
    assert channel.names() == ["goal_request_held"]
    assert not deps.wake.is_set()  # Nothing to plan until the human says yes.


async def test_request_echoed_goal_refuses_a_request_that_needs_no_confirmation() -> None:
    deps, _link, _warden_end = make_queen_deps()
    typed = make_goal_request(deps.clock)

    with pytest.raises(ValueError, match="needs no confirmation"):
        await Queen(deps).request_echoed_goal(typed)

    assert await deps.trail.query(TrailQuery()) == ()
