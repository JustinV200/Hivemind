"""Integration tests for hivemind.workers.runtime.loop.WorkerRuntime: the whole Worker lifecycle.

Every scenario drives a real `WorkerRuntime` over an in-process `MemoryTransport` pair
(`builders.workers.WardenEnd` on one end, the runtime's own `RuntimeDeps.transport` on the
other), running `runtime.run()` as a background task and observing it only through the wire (the
same interface its real Warden uses) plus `runtime.state` and the runtime's own Pheromone Trail.
No real sleeps: every wait is either a `waggle.clock.FakeClock.advance()` or an `asyncio`
primitive (`asyncio.Event`, `asyncio.sleep(0)` to yield the loop). Split by feature (codingrules
14.2/5.1) from `test_loop_shutdown.py`, which proves `stop()` leaves no task behind.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.memory import make_handoff
from builders.workers import (
    RunScript,
    ScriptedWorker,
    WardenEnd,
    make_assignment,
    make_context,
    make_outcome,
    yield_then,
)

from hivemind.memory import Handoff
from hivemind.pheromone.trail.protocol import TrailQuery
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_message_id, new_node_id, new_warden_id, new_worker_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmKind, Intervene, InterventionAction, Question
from waggle.messages.task import (
    TaskAssign,
    TaskCancel,
    TaskOutcome,
    TaskPause,
    TaskResume,
    TaskStage,
)

# Cheap, bounded scheduling yields: enough rounds for the runtime's own tick to process one
# already-queued envelope before a test moves on to a clock-dependent step. Not a real sleep.
_SETTLE_ROUNDS = 20


async def _settle() -> None:
    """Give the event loop `_SETTLE_ROUNDS` scheduling turns, so a queued envelope gets handled."""
    for _ in range(_SETTLE_ROUNDS):
        await asyncio.sleep(0)


async def _wait_until_state(
    runtime: WorkerRuntime, expected: WorkerState, rounds: int = 200
) -> None:
    """Poll `runtime.state` until it equals `expected`.

    Several terminal WorkerState transitions (a crashed role's FAILED aside, which a wire
    AlarmRaised already signals) send no wire message at all (`hivemind.workers.runtime.attempt.
    AttemptManager`'s own module docstring: only a claim ever produces a TaskResult), so there is
    nothing to `wait_for_*` on the wire for them; polling the runtime's own `state` property with
    cheap scheduling yields is the only observable left.
    """
    for _ in range(rounds):
        if runtime.state is expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"runtime never reached {expected}; still {runtime.state}")


def _build(
    clock: FakeClock, script: RunScript, heartbeat_interval_s: float = 5.0
) -> tuple[WorkerRuntime, WardenEnd, ScriptedWorker, WorkerContext]:
    """Wire a WorkerRuntime to a WardenEnd over a fresh MemoryTransport pair."""
    worker_id = new_worker_id(clock)
    warden_id = new_warden_id(clock)
    node_id = new_node_id(clock)
    warden_end, worker_transport = WardenEnd.pair_with(worker_id, warden_id, node_id, clock)
    ctx = make_context(clock=clock, worker_id=worker_id)
    deps = RuntimeDeps(
        transport=worker_transport,
        hop=Hop(sender=worker_id, recipient=warden_id, node_id=node_id),
        heartbeat_interval_s=heartbeat_interval_s,
        clock=clock,
    )
    worker = ScriptedWorker(script)
    runtime = WorkerRuntime(ctx, worker, deps)
    return runtime, warden_end, worker, ctx


# ──────────────────────────────────────────────────────────────────────────────
# Assign, run, claim
# ──────────────────────────────────────────────────────────────────────────────


async def test_assign_runs_role_reports_started_then_claimed_with_trail_events_in_order() -> None:
    clock = FakeClock()
    runtime, warden_end, _worker, ctx = _build(
        clock, yield_then(lambda: make_outcome(summary="done"))
    )
    task = asyncio.create_task(runtime.run())

    assignment = make_assignment(clock=clock)
    await warden_end.send(assignment)
    started = await warden_end.wait_for_progress(TaskStage.STARTED)
    result = await warden_end.wait_for_result()

    assert started.task_id == assignment.task_id
    assert result.outcome is TaskOutcome.CLAIMED
    assert result.summary == "done"
    assert runtime.state is WorkerState.DONE

    events = await ctx.trail.query(TrailQuery(subject_id=ctx.worker_id))
    assert [event.kind for event in events] == ["worker.started", "worker.done"]

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


# ──────────────────────────────────────────────────────────────────────────────
# Handoff and restart
# ──────────────────────────────────────────────────────────────────────────────


async def test_handoff_writes_a_checkpoint_and_restarts_the_role_with_resume_from() -> None:
    clock = FakeClock()
    attempts = {"n": 0}

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        attempts["n"] += 1
        if attempts["n"] == 1:
            assert resume_from is None
            return make_outcome(claimed=False, handoff=make_handoff(goal="continue"))
        assert resume_from is not None
        assert resume_from.goal == "continue"
        return make_outcome(summary="finished after resume")

    runtime, warden_end, _worker, ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    checkpoint = await warden_end.wait_for_progress(TaskStage.CHECKPOINTED)
    result = await warden_end.wait_for_result()

    assert checkpoint.handoff is not None
    assert result.outcome is TaskOutcome.CLAIMED
    assert result.summary == "finished after resume"
    assert attempts["n"] == 2

    events = await ctx.trail.query(TrailQuery(subject_id=ctx.worker_id))
    assert [event.kind for event in events] == [
        "worker.started",
        "worker.handing_off",
        "worker.resumed",
        "worker.done",
    ]

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


# ──────────────────────────────────────────────────────────────────────────────
# A crashing role
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_raising_role_produces_an_alarm_moves_to_failed_and_the_loop_keeps_running() -> (
    None
):
    clock = FakeClock()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        raise RuntimeError("boom")

    runtime, warden_end, _worker, ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    alarm = await warden_end.wait_for_alarm()

    # No TaskResult follows: a Worker's own hop may only ever claim (hivemind.workers.runtime.
    # attempt.AttemptManager's own module docstring); _handle_crashed transitions to FAILED
    # before sending the alarm, so this already holds by the time wait_for_alarm returns.
    assert alarm.kind is AlarmKind.WORKER_CRASHED
    assert "boom" in alarm.detail
    assert runtime.state is WorkerState.FAILED
    assert not task.done()  # The loop keeps running after a crashed attempt.

    events = await ctx.trail.query(TrailQuery(subject_id=ctx.worker_id))
    assert [event.kind for event in events] == ["worker.started", "worker.failed"]

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


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
        ctx.telemetry.note_alarm(AlarmKind.POSTCONDITION_FAILED, "rolled back after applying")
        return make_outcome(claimed=True, summary="done despite the rollback")

    runtime, warden_end, _worker, ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    alarm = await warden_end.wait_for_alarm()
    result = await warden_end.wait_for_result()

    assert alarm.kind is AlarmKind.POSTCONDITION_FAILED
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


# ──────────────────────────────────────────────────────────────────────────────
# TaskCancel with grace
# ──────────────────────────────────────────────────────────────────────────────


async def test_cancel_lets_the_role_finish_within_the_grace_period() -> None:
    clock = FakeClock()
    runtime, warden_end, _worker, _ctx = _build(clock, yield_then(lambda: make_outcome(), ticks=1))
    task = asyncio.create_task(runtime.run())

    assignment = make_assignment(clock=clock)
    await warden_end.send(assignment)
    await warden_end.wait_for_progress(TaskStage.STARTED)

    # A five-second grace the role never needs: it yields once and returns well before that.
    await warden_end.send(
        TaskCancel(task_id=assignment.task_id, grace_s=5.0, reason="no longer needed")
    )
    result = await warden_end.wait_for_result()

    assert result.outcome is TaskOutcome.CLAIMED
    assert runtime.state is WorkerState.DONE

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_cancel_force_cancels_the_role_once_the_grace_period_elapses() -> None:
    clock = FakeClock()
    started = asyncio.Event()
    forever = asyncio.Event()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        started.set()
        await forever.wait()  # Never set; this attempt only ends by being cancelled.
        return make_outcome()  # pragma: no cover

    runtime, warden_end, _worker, _ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    assignment = make_assignment(clock=clock)
    await warden_end.send(assignment)
    await warden_end.wait_for_progress(TaskStage.STARTED)
    await started.wait()

    await warden_end.send(TaskCancel(task_id=assignment.task_id, grace_s=2.0, reason="stop it"))
    await _settle()  # Let the runtime dispatch TaskCancel and schedule the grace-period sleep.
    clock.advance(2.0)  # The grace period elapses; the runtime force-cancels the role task.

    # No TaskResult follows a KILLED transition either (see the raising-role test above); poll
    # runtime.state, the only observable left.
    await _wait_until_state(runtime, WorkerState.KILLED)

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


# ──────────────────────────────────────────────────────────────────────────────
# TaskPause / TaskResume
# ──────────────────────────────────────────────────────────────────────────────


async def test_pause_then_resume_is_observed_by_a_role_awaiting_wait_if_paused() -> None:
    clock = FakeClock()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        # Loops enough times, each yielding the event loop, that a pause requested meanwhile is
        # certain to be observed by some later iteration's wait_if_paused() call.
        for _ in range(100):
            await ctx.telemetry.wait_if_paused()
            await asyncio.sleep(0)
        return make_outcome(summary="resumed and done")

    runtime, warden_end, _worker, _ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    assignment = make_assignment(clock=clock)
    await warden_end.send(assignment)
    await warden_end.wait_for_progress(TaskStage.STARTED)

    await warden_end.send(TaskPause(task_id=assignment.task_id, reason="hold on"))
    await warden_end.wait_for_progress(TaskStage.PAUSED)
    state_after_pause = runtime.state
    assert state_after_pause is WorkerState.PAUSED

    await warden_end.send(
        TaskResume(task_id=assignment.task_id, attempt=1, resume_from=None, slot=None, reason="go")
    )
    await warden_end.wait_for_progress(TaskStage.RESUMED)
    result = await warden_end.wait_for_result()

    assert result.summary == "resumed and done"
    state_after_result = runtime.state
    assert state_after_result is WorkerState.DONE

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


# ──────────────────────────────────────────────────────────────────────────────
# Intervene
# ──────────────────────────────────────────────────────────────────────────────


async def test_intervene_compact_sets_handoff_requested_and_continues_after_the_checkpoint() -> (
    None
):
    clock = FakeClock()
    reached = asyncio.Event()
    attempts = {"n": 0}

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        attempts["n"] += 1
        if attempts["n"] == 1:
            reached.set()
            # ASYNC110: no asyncio.Event exists for this plain bool flag; polling it is exactly
            # what a real role would do between turns (hivemind.workers.telemetry's own module
            # docstring: "a role calls it between turns").
            while not ctx.telemetry.handoff_requested:  # noqa: ASYNC110
                await asyncio.sleep(0)
            return make_outcome(claimed=False, handoff=make_handoff())
        return make_outcome(summary="second attempt")

    runtime, warden_end, _worker, _ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    await reached.wait()

    await warden_end.send(
        Intervene(
            action=InterventionAction.COMPACT,
            subject=None,
            task_id=None,
            slot=None,
            alarm_id=None,
            reason="compact now",
        )
    )
    result = await warden_end.wait_for_result()

    assert result.outcome is TaskOutcome.CLAIMED
    assert result.summary == "second attempt"  # Proves it restarted rather than stopping.
    assert attempts["n"] == 2

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.parametrize(
    "action", [InterventionAction.HANDOFF, InterventionAction.REBIND, InterventionAction.TAKEOVER]
)
async def test_intervene_handoff_rebind_takeover_stop_the_attempt_after_the_checkpoint(
    action: InterventionAction,
) -> None:
    clock = FakeClock()
    reached = asyncio.Event()
    attempts = {"n": 0}

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        attempts["n"] += 1
        reached.set()
        # ASYNC110: see the equivalent loop above; no Event exists for this plain bool flag.
        while not ctx.telemetry.handoff_requested:  # noqa: ASYNC110
            await asyncio.sleep(0)
        return make_outcome(claimed=False, handoff=make_handoff())

    runtime, warden_end, _worker, _ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    await reached.wait()

    slot = "WORKER" if action is InterventionAction.REBIND else None
    await warden_end.send(
        Intervene(
            action=action, subject=None, task_id=None, slot=slot, alarm_id=None, reason="stop now"
        )
    )
    checkpoint = await warden_end.wait_for_progress(TaskStage.CHECKPOINTED)

    # No TaskResult follows: the Warden pulled this lever itself, so it already knows to stop,
    # and the checkpoint above carries the HandoffRef it needs.
    assert checkpoint.handoff is not None
    await _wait_until_state(runtime, WorkerState.DONE)
    assert attempts["n"] == 1  # Never restarted.

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_intervene_cancel_force_cancels_immediately_with_no_grace() -> None:
    clock = FakeClock()
    started = asyncio.Event()
    forever = asyncio.Event()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        started.set()
        await forever.wait()
        return make_outcome()  # pragma: no cover

    runtime, warden_end, _worker, _ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    assignment = make_assignment(clock=clock)
    await warden_end.send(assignment)
    await warden_end.wait_for_progress(TaskStage.STARTED)
    await started.wait()

    await warden_end.send(
        Intervene(
            action=InterventionAction.CANCEL,
            subject=None,
            task_id=None,
            slot=None,
            alarm_id=None,
            reason="abort",
        )
    )
    await _wait_until_state(runtime, WorkerState.KILLED)

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


# ──────────────────────────────────────────────────────────────────────────────
# Heartbeats
# ──────────────────────────────────────────────────────────────────────────────


async def test_heartbeat_fires_on_the_configured_interval_with_current_telemetry() -> None:
    clock = FakeClock()
    runtime, warden_end, _worker, _ctx = _build(
        clock, yield_then(lambda: make_outcome()), heartbeat_interval_s=10.0
    )
    task = asyncio.create_task(runtime.run())

    await _settle()  # Let the runtime schedule its first heartbeat wait before advancing time.
    clock.advance(10.0)
    heartbeat = await warden_end.wait_for_heartbeat()

    assert heartbeat.interval_s == pytest.approx(10.0)
    assert heartbeat.task_id is None  # No TaskAssign was ever sent in this test.
    assert heartbeat.worker_state is not None
    assert heartbeat.worker_state.value == "SPAWNED"

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


# ──────────────────────────────────────────────────────────────────────────────
# Question / Answer
# ──────────────────────────────────────────────────────────────────────────────


async def test_question_answer_round_trip_blocks_and_unblocks_the_role() -> None:
    clock = FakeClock()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        question = Question(
            question_id=new_message_id(clock),
            task_id=assignment.task_id,
            asked_by=ctx.worker_id,
            text="Which environment?",
            options=(),
            clearance=WireHoneyClearance.C1,
            asked_at=clock.now(),
        )
        answer = await ctx.asker.ask(question)
        return make_outcome(summary=f"answer: {answer.text}")

    runtime, warden_end, _worker, _ctx = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    question = await warden_end.wait_for_question()
    await warden_end.answer(question, "staging")
    result = await warden_end.wait_for_result()

    assert result.summary == "answer: staging"

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


# ──────────────────────────────────────────────────────────────────────────────
# stop() and a closed transport
# ──────────────────────────────────────────────────────────────────────────────


async def test_stop_closes_the_transport_cleanly_with_no_active_attempt() -> None:
    clock = FakeClock()
    runtime, _warden_end, _worker, _ctx = _build(clock, yield_then(lambda: make_outcome()))
    task = asyncio.create_task(runtime.run())

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)

    assert task.done()
    assert not task.cancelled()
    assert task.exception() is None


async def test_a_closed_transport_ends_the_runtime() -> None:
    # Policy documented on hivemind.workers.runtime.loop.WorkerRuntime._on_transport_closed: the
    # transport ending, cleanly or otherwise, always ends this runtime.
    clock = FakeClock()
    runtime, warden_end, _worker, _ctx = _build(clock, yield_then(lambda: make_outcome()))
    task = asyncio.create_task(runtime.run())

    await warden_end.close()

    await asyncio.wait_for(task, timeout=1)
    assert task.done()
