"""Tests for the Queen's side of a quarantine: she orders one, and holds the task when told.

Roadmap step 10.6c. A Warden that quarantined a bee reports its task at stage PAUSED and raises a
SECURITY Alarm; a running Queen moves the task to PAUSED in the Brood Chamber (withdrawing a
question the bee left it blocked on first) and sends the SECURITY Alarm on to the human. With a
policy row naming QUARANTINE, her own escalation sends the Warden of the Alarm's task an
`Intervene(QUARANTINE)` built from the Alarm (its bee, task and trail event), and an Alarm that
cannot scope one reaches the human instead. `Queen.intervene` carries a whole Quarantine lever to
a Warden. Her autopilot row for a `TaskProgress` is pinned too.

Fits into the Hive:
    Mirrors src/hivemind/queen/quarantine/ and the quarantine rows of src/hivemind/queen/
    autopilot/table.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.quarantine for order_quarantine and hold_task.
    - hivemind.wardens.quarantine for the Warden's one code path these reports come from.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.queen import WardenEnd, make_queen_deps, plan_responder
from builders.supervision import make_inbox_item
from builders.tasks import make_task

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.autopilot import QueenAction, decide
from hivemind.queen.deps import QueenDeps
from hivemind.queen.quarantine import hold_task
from hivemind.queen.queen import Queen
from hivemind.supervision import AlarmKind as HiveAlarmKind
from hivemind.supervision import (
    EscalationPolicy,
    PolicyAction,
    PolicyRule,
    Quarantine,
    load_policy,
)
from hivemind.supervision.attendant import InboxKind
from waggle.ids import EventId, TaskId, new_alarm_id, new_event_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised, InterventionAction
from waggle.messages.task import TaskProgress, TaskStage

_TIMEOUT_S = 5.0  # Bound on stopping a Queen's loop in a test.


def _plan(goal: str) -> dict[str, object]:
    """A one-task plan, so a submitted goal is one RUNNING task on the one attached Warden."""
    acceptance: dict[str, object] = {
        "kind": "FILE_EXISTS",
        "subject": "scratch/done.txt",
        "argv": [],
        "expected": None,
    }
    task = {
        "key": "root",
        "title": "Root task",
        "objective": f"Do: {goal}",
        "acceptance": [acceptance],
    }
    return {"tasks": [{**task, "needs": {}, "clearance": "C1", "depends_on": []}]}


def _held(task_id: TaskId, stage: TaskStage = TaskStage.PAUSED) -> TaskProgress:
    """A Warden's report on `task_id` at `stage`, as a quarantine sends it."""
    return TaskProgress(
        task_id=task_id,
        attempt=1,
        stage=stage,
        summary="Held: the bee is quarantined.",
        clearance=WireHoneyClearance.C1,
        fraction_done=None,
        handoff=None,
    )


def _alarm(deps: QueenDeps, task_id: TaskId, event_id: EventId | None) -> AlarmRaised:
    """A SECURITY Alarm about one bee on `task_id`, naming `event_id` as what explains it."""
    return AlarmRaised(
        alarm_id=new_alarm_id(deps.clock),
        kind=AlarmKind.SECURITY,
        severity=AlarmSeverity.CRITICAL,
        origin=new_worker_id(deps.clock),
        attempts=0,
        raised_at=deps.clock.now(),
        context=AlarmContext(
            task_id=task_id,
            cell_id=None,
            worker_id=new_worker_id(deps.clock),
            event_id=event_id,
            handoff=None,
        ),
        detail="A scanner correlation flagged this bee.",
        clearance=WireHoneyClearance.C1,
        reason="Security events always go up.",
    )


async def _running_queen(
    policy: EscalationPolicy | None = None,
) -> tuple[Queen, QueenDeps, WardenEnd, TaskId, asyncio.Task[None]]:
    """A running Queen, on `policy` (the shipped one when omitted), with one goal dispatched."""
    provider = FakeLLMProvider(responder=plan_responder(_plan))
    rows = policy if policy is not None else load_policy()
    deps, link, warden_end = make_queen_deps(fake_provider=provider, policy=rows)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    return queen, deps, warden_end, goal_id, asyncio.ensure_future(queen.run())


async def _stop(queen: Queen, run_task: asyncio.Task[None], warden_end: WardenEnd) -> None:
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=_TIMEOUT_S)
    await warden_end.close()


async def _until(condition: Callable[[], Awaitable[bool]], limit: int = 400) -> None:
    """Yield the event loop until the async `condition()` holds, or fail."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("The condition never held.")


async def _status(deps: QueenDeps, task_id: TaskId) -> TaskStatus:
    return (await deps.chamber.get(task_id)).status


async def test_a_wardens_held_report_pauses_the_task_and_its_security_alarm_reaches_the_human() -> (
    None
):
    queen, deps, warden_end, goal_id, run_task = await _running_queen()

    await warden_end.send(_held(goal_id))
    await warden_end.send(_alarm(deps, goal_id, new_event_id(deps.clock)))
    await _until(lambda: _is(deps, goal_id, TaskStatus.PAUSED))
    await _until(lambda: _escalated(queen))

    assert [alarm.kind for alarm in queen.human_inbox.alarms] == [HiveAlarmKind.SECURITY]
    assert not warden_end.intervenes  # The shipped policy escalates; it never orders more.
    await _stop(queen, run_task, warden_end)


async def test_a_queen_policy_row_orders_the_wardens_quarantine_from_the_alarm() -> None:
    policy = EscalationPolicy(
        rules=(
            PolicyRule(kind=HiveAlarmKind.SECURITY, min_attempts=1, action=PolicyAction.QUARANTINE),
        ),
        default=PolicyAction.ESCALATE,
    )
    queen, deps, warden_end, goal_id, run_task = await _running_queen(policy=policy)
    alarm = _alarm(deps, goal_id, new_event_id(deps.clock))

    await warden_end.send(alarm)
    intervene = await asyncio.wait_for(warden_end.wait_for_intervene(), timeout=_TIMEOUT_S)

    assert intervene.action is InterventionAction.QUARANTINE
    assert (intervene.subject, intervene.task_id) == (alarm.context.worker_id, goal_id)
    assert intervene.suspect_episode_id == alarm.context.event_id
    assert intervene.alarm_id == alarm.alarm_id
    decided = await deps.trail.query(TrailQuery(kind="queen.decided", subject_id=goal_id))
    assert "QUARANTINE_BEE" in [event.payload.get("action") for event in decided]
    assert not queen.human_inbox.alarms
    await _stop(queen, run_task, warden_end)


async def test_an_alarm_that_cannot_scope_a_quarantine_reaches_the_human_instead() -> None:
    policy = EscalationPolicy(
        rules=(
            PolicyRule(kind=HiveAlarmKind.SECURITY, min_attempts=1, action=PolicyAction.QUARANTINE),
        ),
        default=PolicyAction.ESCALATE,
    )
    queen, deps, warden_end, goal_id, run_task = await _running_queen(policy=policy)

    await warden_end.send(_alarm(deps, goal_id, None))  # Names no event to taint from.
    await _until(lambda: _escalated(queen))

    assert not warden_end.intervenes
    await _stop(queen, run_task, warden_end)


async def test_queen_intervene_carries_the_whole_quarantine_lever() -> None:
    queen, deps, warden_end, goal_id, run_task = await _running_queen()
    bee, episode = new_worker_id(deps.clock), new_event_id(deps.clock)
    lever = Quarantine(reason="Guard report.", bee=bee, task_id=goal_id, suspect_episode_id=episode)

    await queen.intervene(queen.wardens[0].warden_id, lever)
    intervene = await asyncio.wait_for(warden_end.wait_for_intervene(), timeout=_TIMEOUT_S)

    assert (intervene.action, intervene.subject) == (InterventionAction.QUARANTINE, bee)
    assert (intervene.task_id, intervene.suspect_episode_id) == (goal_id, episode)
    await _stop(queen, run_task, warden_end)


async def test_holding_a_blocked_task_withdraws_its_question_before_pausing_it() -> None:
    queen, deps, warden_end, goal_id, run_task = await _running_queen()
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=_TIMEOUT_S)
    await deps.chamber.ask(goal_id, new_worker_id(deps.clock), "Which season?")

    await hold_task(deps, _held(goal_id))
    await hold_task(deps, _held(goal_id))  # A repeat changes nothing.

    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.PAUSED and task.pending_question_id is None
    kinds = [event.kind for event in await deps.trail.query(TrailQuery(subject_id=goal_id))]
    assert kinds.count("task.paused") == 1 and "task.question_withdrawn" in kinds
    await warden_end.close()


def test_the_queens_table_pauses_on_a_held_report_and_records_every_other_stage() -> None:
    running = make_task(TaskStatus.RUNNING)
    finished = make_task(TaskStatus.SUCCEEDED)
    policy = EscalationPolicy(rules=(), default=PolicyAction.ESCALATE)

    def decided(progress: TaskProgress, task: object) -> QueenAction:
        item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, payload=progress, task_id=progress.task_id)
        return decide(item, task, 1, policy, 3)  # type: ignore[arg-type]

    assert decided(_held(running.id), running) is QueenAction.PAUSE_TASK
    assert decided(_held(running.id, TaskStage.WORKING), running) is QueenAction.RECORD
    assert decided(_held(finished.id), finished) is QueenAction.RECORD


async def _is(deps: QueenDeps, task_id: TaskId, status: TaskStatus) -> bool:
    return await _status(deps, task_id) is status


async def _escalated(queen: Queen) -> bool:
    return bool(queen.human_inbox.alarms)
