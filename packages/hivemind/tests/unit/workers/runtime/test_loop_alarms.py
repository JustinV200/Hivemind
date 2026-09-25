"""Tests for hivemind.workers.runtime.loop.WorkerRuntime: Alarms reach the trail and the Warden.

Split by feature (codingrules 14.2/5.1) from `test_loop.py`, which covers the rest of the Worker
lifecycle over the same in-process `MemoryTransport` pair. Every Alarm a role notes on its
telemetry must land on the Pheromone Trail before it leaves the link (fix 1), still reach the
Warden when noted on the attempt's last tool call (fix 2), and, when the link has already
dropped, be logged rather than crash the Worker on its way out (phase 7 handoff, open item 8).

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/loop.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/workers/runtime/test_loop.py for the rest of the runtime's lifecycle.
"""

from __future__ import annotations

import asyncio

from builders.workers import (
    RunScript,
    ScriptedWorker,
    WardenEnd,
    make_assignment,
    make_context,
    make_outcome,
)

from hivemind.memory import Handoff
from hivemind.pheromone.trail.protocol import TrailQuery
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_node_id, new_warden_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign, TaskOutcome


def _build(
    clock: FakeClock, script: RunScript
) -> tuple[WorkerRuntime, WardenEnd, ScriptedWorker, WorkerContext]:
    """Wire a WorkerRuntime to a WardenEnd over a fresh pair (mirrors test_loop.py's `_build`)."""
    worker_id = new_worker_id(clock)
    warden_id = new_warden_id(clock)
    node_id = new_node_id(clock)
    warden_end, worker_transport = WardenEnd.pair_with(worker_id, warden_id, node_id, clock)
    ctx = make_context(clock=clock, worker_id=worker_id)
    deps = RuntimeDeps(
        transport=worker_transport,
        hop=Hop(sender=worker_id, recipient=warden_id, node_id=node_id),
        heartbeat_interval_s=5.0,
        clock=clock,
    )
    worker = ScriptedWorker(script)
    return WorkerRuntime(ctx, worker, deps), warden_end, worker, ctx


# ──────────────────────────────────────────────────────────────────────────────
# Alarms reaching the trail (fix 1), and a pending Alarm flushed at a terminal
# transition rather than lost with it (fix 2)
# ──────────────────────────────────────────────────────────────────────────────


async def test_send_alarm_records_alarm_raised_on_the_trail_before_it_leaves_the_link() -> None:
    """Fix 1: a rolled-back gate outcome ends with alarm.raised on the trail first.

    It lands before the AlarmRaised leaves the link: Reporter.send_alarm now calls
    record_alarm_event first.
    """
    clock = FakeClock()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        ctx.telemetry.note_alarm(AlarmKind.POSTCONDITION_FAILED, "postconditions failed to hold")
        return make_outcome(summary="done")

    runtime, warden_end, _worker, ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    alarm = await warden_end.wait_for_alarm()

    # record_alarm_event is awaited before the mailbox.send call inside Reporter.send_alarm
    # (fix 1's own ordering), so by the time the wire AlarmRaised is observed here, the trail
    # write for the exact same alarm id has already completed.
    events = await ctx.trail.query(TrailQuery(subject_id=alarm.alarm_id))
    assert [event.kind for event in events] == ["alarm.raised"]

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_an_alarm_noted_on_the_last_tool_call_still_reaches_the_warden_when_claimed() -> None:
    """Fix 2: a role that notes an Alarm then returns claimed=True still yields the AlarmRaised.

    The old runtime only drained TelemetryTracker.take_pending_alarms() at the top of _tick, so a
    pending Alarm noted just before the role's own coroutine returned was still sitting in the
    queue when the same tick's on_finished() moved the Worker straight to DONE; nothing drained
    it again afterwards. AttemptManager now flushes the queue before every terminal transition, so
    the AlarmRaised goes out first.
    """
    clock = FakeClock()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        # With its own reason and severity, which the flush must carry over (a judge's REJECT).
        reason, severity = "the step's screen was misread", AlarmSeverity.CRITICAL
        ctx.telemetry.note_alarm(
            AlarmKind.POSTCONDITION_FAILED, "rolled back", reason=reason, severity=severity
        )
        return make_outcome(claimed=True, summary="done despite the rollback")

    runtime, warden_end, _worker, ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    alarm = await warden_end.wait_for_alarm()
    result = await warden_end.wait_for_result()

    assert alarm.kind is AlarmKind.POSTCONDITION_FAILED
    assert (alarm.reason, alarm.severity) == (
        "the step's screen was misread",
        AlarmSeverity.CRITICAL,
    )
    assert result.outcome is TaskOutcome.CLAIMED
    assert result.summary == "done despite the rollback"
    assert runtime.state is WorkerState.DONE

    # The flush's own alarm.raised (fix 1) precedes worker.done in the trail this Worker itself
    # writes; the AlarmEvent carries a different subject_id (the alarm id), so it never appears
    # in this worker_id-scoped query, but the ordering the flush guarantees is what let the
    # AlarmRaised reach warden_end above before this attempt closed.
    events = await ctx.trail.query(TrailQuery(subject_id=ctx.worker_id))
    assert [event.kind for event in events] == ["worker.started", "worker.done"]

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_a_dropped_link_with_an_alarm_pending_ends_the_worker_killed_not_crashed() -> None:
    """Phase 7 handoff open item 8: a dropped link's flush logs the unsent Alarm, never raises.

    The link drops mid-attempt with an Alarm noted and not yet flushed. The Worker's receive side
    sees the drop and flushes on the way out; the Alarm's wire send raises ConnectionLostError,
    which used to escape `run()`. It is now logged like a clean close: alarm.raised is still on
    the trail, and the Worker ends KILLED with `run()` returning normally.
    """
    clock = FakeClock()
    noted = asyncio.Event()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        ctx.telemetry.note_alarm(AlarmKind.POSTCONDITION_FAILED, "rolled back after applying")
        noted.set()
        # Mid-attempt until the link drops under it; the runtime cancels this on the way out.
        await asyncio.Event().wait()
        return make_outcome()

    runtime, warden_end, _worker, ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())
    await warden_end.send(make_assignment(clock=clock))
    await noted.wait()

    warden_end.drop()
    await asyncio.wait_for(task, timeout=1)

    assert runtime.state is WorkerState.KILLED
    raised = await ctx.trail.query(TrailQuery(kind="alarm.raised"))
    assert len(raised) == 1
