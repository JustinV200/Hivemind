"""Test hivemind.queen.chat.door: the Hive Entrance's two newest ways in, through the Queen.

``escalate_to_human`` puts an Alarm the Hive itself raised (the Entrance's remote listener failed)
in front of the human as an escalated Alarm does: in her inbox, on the trail, in the chat and on
every device. ``cancel_goal`` cancels what of a goal has not started (a revoked device's goal) and
says whether anything is left running.

Fits into the Hive:
    Mirrors src/hivemind/queen/chat/door.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.human import RecordingHumanChannel
from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.chat import ChatKind, ChatQuery
from hivemind.queen.queen import Queen
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState
from waggle.ids import new_alarm_id
from waggle.messages.supervision import AlarmContext


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


async def test_cancel_goal_cancels_what_has_not_started_and_says_what_still_runs() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_two_step_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()

    ended = await queen.cancel_goal(goal_id, "its device was revoked")

    statuses = {
        task.spec.title: task.status
        for task in await deps.chamber.list(TaskFilter(goal_id=goal_id))
    }
    assert ended is False
    assert statuses["Polish"] is TaskStatus.CANCELLED
    assert statuses["Draft"] is not TaskStatus.CANCELLED
    await warden_end.close()
