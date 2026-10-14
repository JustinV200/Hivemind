"""Tests for hivemind.workers.runtime.loop: a role that ends while its Worker is held by a pause.

A `TaskPause` only asks the role to wait at its next `wait_if_paused()`; a role that was already
inside a model call when the pause arrived keeps running until it gets there, and that call can
fail (Isolation cuts a Cell's egress, a provider goes down: the two situations that pause bees).
The role's own end is the role's event, not the supervisor's, so it is reported exactly as one
that happens while RUNNING: a crash is `worker.failed` and an Alarm, a claim is `worker.done` and
a result. Before, the table had no edge out of PAUSED but RUNNING and KILLED, so the end raised
`InvalidWorkerTransitionError` out of the tick, killed the runtime, and left the error stored in
its task for whichever Warden next reaped it. The last case proves the other half: whatever does
escape a runtime's loop ends it as one typed `WorkerRuntimeError`, never a bare error in the task.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/loop.py and attempt.py (codingrules section 3), split by
    feature from test_loop.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.state for the PAUSED edges this proves.
    - tests.unit.wardens.spawn.test_spawn_stop for the Warden's half: a runtime that did die.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from builders.trail_faults import FailingTrail
from builders.workers import (
    ScriptedWorker,
    WardenEnd,
    make_assignment,
    make_context,
    make_outcome,
)

from hivemind.llm.errors import ProviderUnavailableError
from hivemind.memory import Handoff
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.errors import WorkerRuntimeError
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_node_id, new_warden_id, new_worker_id
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign, TaskOutcome, TaskPause, TaskStage

# The heartbeat interval is irrelevant to these scenarios; one long enough that none is due.
_HEARTBEAT_S = 3_600.0
# How long a scenario waits for the runtime to say something: it answers within a few scheduling
# turns, so this only bounds a run where it never does (a dead runtime), failing it instead of
# hanging the suite.
_WAIT_S = 5.0


def _build(
    clock: FakeClock, ending: Callable[[], WorkerOutcome], *, trail: MemoryPheromoneTrail | None
) -> tuple[WorkerRuntime, WardenEnd, WorkerContext, asyncio.Event, asyncio.Event]:
    """Wire a role that waits inside a "call", then ends by `ending`, to a runtime and a WardenEnd.

    Returns the runtime (loop not started), the Warden's end, the context and two events: the
    first is set once the role is inside its call, the second is the call returning.
    """
    in_call, call_returns = asyncio.Event(), asyncio.Event()

    async def script(
        _ctx: WorkerContext, _assign: TaskAssign, _resume: Handoff | None
    ) -> WorkerOutcome:
        in_call.set()
        await call_returns.wait()  # The call the pause finds the role in.
        return ending()

    worker_id, warden_id, node_id = new_worker_id(clock), new_warden_id(clock), new_node_id(clock)
    warden_end, worker_transport = WardenEnd.pair_with(worker_id, warden_id, node_id, clock)
    overrides = {"trail": trail} if trail is not None else {}
    ctx = make_context(clock=clock, worker_id=worker_id, **overrides)
    deps = RuntimeDeps(
        transport=worker_transport,
        hop=Hop(sender=worker_id, recipient=warden_id, node_id=node_id),
        heartbeat_interval_s=_HEARTBEAT_S,
        clock=clock,
    )
    runtime = WorkerRuntime(ctx, ScriptedWorker(script), deps)
    return runtime, warden_end, ctx, in_call, call_returns


async def _assign_and_pause(
    clock: FakeClock, warden_end: WardenEnd, in_call: asyncio.Event
) -> None:
    """Assign the task, wait until its role is inside the call, then send the pause."""
    assignment = make_assignment(clock=clock)
    await warden_end.send(assignment)
    await asyncio.wait_for(in_call.wait(), _WAIT_S)
    await warden_end.send(TaskPause(task_id=assignment.task_id, reason="hold on"))


def _state_of(runtime: WorkerRuntime) -> WorkerState:
    """Read the runtime's state afresh, so a type checker does not carry an earlier check over."""
    return runtime.state


def _crash() -> WorkerOutcome:
    """End the role as a call that failed does: egress was cut while the call was in flight."""
    raise ProviderUnavailableError("fake", "egress was cut while the call was in flight")


def _claim() -> WorkerOutcome:
    """End the role as a call that returned the final answer does."""
    return make_outcome(summary="finished in the call the pause found it in")


async def test_a_role_that_crashes_while_its_worker_is_paused_is_reported_as_a_crash() -> None:
    clock = FakeClock()
    runtime, warden_end, ctx, in_call, call_returns = _build(clock, _crash, trail=None)
    loop = asyncio.create_task(runtime.run())
    await _assign_and_pause(clock, warden_end, in_call)
    await asyncio.wait_for(warden_end.wait_for_progress(TaskStage.PAUSED), _WAIT_S)
    assert _state_of(runtime) is WorkerState.PAUSED

    call_returns.set()
    alarm = await asyncio.wait_for(warden_end.wait_for_alarm(), _WAIT_S)

    # The crash is the Warden's to judge, as it would be had the pause arrived a moment later.
    assert alarm.kind is AlarmKind.PROVIDER_UNAVAILABLE
    assert _state_of(runtime) is WorkerState.FAILED
    assert not loop.done()  # The runtime lives on: nothing is stored in its task.
    kinds = [e.kind for e in await ctx.trail.query(TrailQuery(subject_id=ctx.worker_id))]
    assert kinds == ["worker.started", "worker.paused", "worker.failed"]
    runtime.stop()
    await asyncio.wait_for(loop, timeout=_WAIT_S)  # Returns cleanly: no error to reap from it.


async def test_a_role_that_claims_while_its_worker_is_paused_is_reported_as_done() -> None:
    clock = FakeClock()
    runtime, warden_end, ctx, in_call, call_returns = _build(clock, _claim, trail=None)
    loop = asyncio.create_task(runtime.run())
    await _assign_and_pause(clock, warden_end, in_call)
    await asyncio.wait_for(warden_end.wait_for_progress(TaskStage.PAUSED), _WAIT_S)

    call_returns.set()
    result = await asyncio.wait_for(warden_end.wait_for_result(), _WAIT_S)

    assert result.outcome is TaskOutcome.CLAIMED
    assert _state_of(runtime) is WorkerState.DONE
    assert not loop.done()
    kinds = [e.kind for e in await ctx.trail.query(TrailQuery(subject_id=ctx.worker_id))]
    assert kinds == ["worker.started", "worker.paused", "worker.done"]
    runtime.stop()
    await asyncio.wait_for(loop, timeout=_WAIT_S)


async def test_a_runtime_whose_trail_fails_under_it_ends_as_a_typed_error_with_its_cause() -> None:
    clock = FakeClock()
    trail = FailingTrail(clock, failing_kind="worker.paused")  # The disk fills as it pauses.
    runtime, warden_end, _ctx, in_call, _call_returns = _build(clock, _claim, trail=trail)
    loop = asyncio.create_task(runtime.run())

    await _assign_and_pause(clock, warden_end, in_call)

    # What the Warden finds stored in the task it reaps: one typed error, the original chained.
    with pytest.raises(WorkerRuntimeError) as stored:
        await asyncio.wait_for(loop, timeout=_WAIT_S)
    assert isinstance(stored.value.__cause__, OSError)
    assert stored.value.worker_id == runtime._ctx.worker_id
    assert stored.value.state is WorkerState.PAUSED  # Where the machine stood when it died.
