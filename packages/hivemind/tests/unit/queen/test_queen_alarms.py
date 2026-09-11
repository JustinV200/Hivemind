"""Tests for hivemind.queen.queen.Queen: handling a Warden-forwarded AlarmRaised.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_dispatch.py, test_queen_results.py, test_queen_questions.py,
    test_queen_liveness.py, test_queen_supervisor.py and test_queen_invariants.py. Exercises
    hivemind.queen.ticks.alarms together with the Queen's own tick, over the *real*
    supervision/defaults/default-policy.toml `make_queen_deps` loads -- the same table
    `hivemind.queen.autopilot.table.decide` consults in production -- so these two tests double
    as roadmap step 3.22 scenario (c)'s own unit-level rehearsal: a PROVIDER_UNAVAILABLE Alarm
    REBINDs once, and a GRANT_EXCEEDED Alarm (or a PROVIDER_UNAVAILABLE past the Queen's own
    ceiling) escalates to the human.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.alarms for the module under test.
    - supervision/defaults/default-policy.toml for the real rules these two Alarm kinds exercise.
    - .claude/roadmap.md step 3.22 scenario (c) for the rebind-then-completes e2e this rehearses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.queen import make_queen_deps, plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.queen import Queen
from waggle.clock import Clock
from waggle.ids import TaskId, new_alarm_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised, InterventionAction


def _single_task_plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/done.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def _alarm(clock: Clock, *, kind: AlarmKind, task_id: TaskId) -> AlarmRaised:
    return AlarmRaised(
        alarm_id=new_alarm_id(clock),
        kind=kind,
        severity=AlarmSeverity.WARNING,
        origin=new_worker_id(clock),
        attempts=0,
        raised_at=clock.now(),
        context=AlarmContext(
            task_id=task_id, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail="The bound provider did not respond.",
        clearance=WireHoneyClearance.C1,
        reason="Cannot resolve locally.",
    )


async def test_provider_unavailable_alarm_rebinds_to_the_worker_slots_own_fallback() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    # PROVIDER_UNAVAILABLE, min_attempts=1 -> REBIND (default-policy.toml); attempts defaults to
    # 1 (the Queen's own counter has no entry yet), which clears that row and is still under the
    # default alarm_attempt_limit (3), so the ceiling never overrides the policy's own choice.
    alarm = _alarm(deps.clock, kind=AlarmKind.PROVIDER_UNAVAILABLE, task_id=goal_id)
    await warden_end.send(alarm)
    intervene = await warden_end.wait_for_intervene()

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert intervene.action is InterventionAction.REBIND
    assert intervene.task_id == goal_id
    assert intervene.slot == "WORKER"  # ModelSlot.WORKER.to_wire(); never a lowercase binding key.
    assert intervene.alarm_id == alarm.alarm_id
    assert not queen.human_inbox.alarms  # A successful REBIND never reaches the human.
    await warden_end.close()


async def test_grant_exceeded_alarm_escalates_straight_to_the_human() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    # GRANT_EXCEEDED, min_attempts=1 -> ESCALATE (default-policy.toml): only the Queen divides
    # Forage, so a Warden can never fix this locally.
    alarm = _alarm(deps.clock, kind=AlarmKind.GRANT_EXCEEDED, task_id=goal_id)
    await warden_end.send(alarm)

    async def _escalated() -> bool:
        return bool(queen.human_inbox.alarms)

    await _wait_until(_escalated)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert not warden_end.intervenes  # Escalated, never rebound.
    escalated = queen.human_inbox.alarms
    assert len(escalated) == 1
    assert escalated[0].id == alarm.alarm_id
    assert escalated[0].kind.value == "GRANT_EXCEEDED"
    await warden_end.close()


async def _wait_until(condition: Callable[[], Awaitable[bool]], limit: int = 200) -> None:
    """Yield the event loop until `condition()` (an async callable) is True, or give up."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")
