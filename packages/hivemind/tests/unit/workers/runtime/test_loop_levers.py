"""Tests for WorkerRuntime's levers the Hive has no Worker-side meaning for (roadmap step 10.6c).

A Quarantine is its Warden's to carry out and is never relayed to a Worker; one that arrives anyway
stops the role at once. An intervention the Hive's union has no lever for (RELEASE_LEASE, a
Warden's own order) is refused and logged, never read as a cancel (ADR-0035), and the Worker keeps
working. Split by feature (codingrules 14.2) from `test_loop.py`, whose scenario shape this reuses.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/loop.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.loop for `_pull_lever`, the function under test.
    - hivemind.supervision.intervention.from_wire for the refusal it relies on.
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
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_event_id, new_node_id, new_task_id, new_warden_id, new_worker_id
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskAssign, TaskStage

_ROUNDS = 200  # Cheap scheduling yields to wait for a state; never a real sleep.
_TASK_ID = new_task_id(FakeClock())  # The one task every scenario here assigns.


def _build(clock: FakeClock, script: RunScript) -> tuple[WorkerRuntime, WardenEnd]:
    """Wire a WorkerRuntime to a WardenEnd over a fresh MemoryTransport pair."""
    worker_id, warden_id, node_id = (
        new_worker_id(clock),
        new_warden_id(clock),
        new_node_id(clock),
    )
    warden_end, worker_transport = WardenEnd.pair_with(worker_id, warden_id, node_id, clock)
    deps = RuntimeDeps(
        transport=worker_transport,
        hop=Hop(sender=worker_id, recipient=warden_id, node_id=node_id),
        heartbeat_interval_s=5.0,
        clock=clock,
    )
    ctx = make_context(clock=clock, worker_id=worker_id)
    return WorkerRuntime(ctx, ScriptedWorker(script), deps), warden_end


async def _wait_until_state(runtime: WorkerRuntime, expected: WorkerState) -> None:
    """Poll `runtime.state` with scheduling yields until it equals `expected`."""
    for _ in range(_ROUNDS):
        if runtime.state is expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"runtime never reached {expected}; still {runtime.state}")


async def _started_forever(clock: FakeClock) -> tuple[WorkerRuntime, WardenEnd, asyncio.Task[None]]:
    """Start a runtime whose role runs until cancelled, and wait until it has started."""
    started, forever = asyncio.Event(), asyncio.Event()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        started.set()
        await forever.wait()
        return make_outcome()  # pragma: no cover - the role only ever ends cancelled here

    runtime, warden_end = _build(clock, script)
    task = asyncio.create_task(runtime.run())
    await warden_end.send(make_assignment(clock=clock, task_id=_TASK_ID))
    await warden_end.wait_for_progress(TaskStage.STARTED)
    await started.wait()
    return runtime, warden_end, task


async def test_a_quarantine_relayed_to_a_worker_stops_its_role_at_once() -> None:
    clock = FakeClock()
    runtime, warden_end, task = await _started_forever(clock)
    lever = Intervene(
        action=InterventionAction.QUARANTINE,
        subject=None,
        task_id=_TASK_ID,
        slot=None,
        alarm_id=None,
        reason="relayed by mistake",
        suspect_episode_id=new_event_id(clock),
    )

    await warden_end.send(lever)
    await _wait_until_state(runtime, WorkerState.KILLED)

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)


async def test_a_lever_the_hive_does_not_model_is_refused_and_the_worker_keeps_working() -> None:
    clock = FakeClock()
    runtime, warden_end, task = await _started_forever(clock)
    release = Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,
        task_id=None,
        slot=None,
        alarm_id=None,
        reason="a Warden's own order, never a Worker's",
    )

    await warden_end.send(release)
    for _ in range(_ROUNDS):
        await asyncio.sleep(0)

    # Before ADR-0035's rule this read as a Cancel and killed the attempt.
    assert runtime.state is WorkerState.RUNNING
    runtime.stop()
    await asyncio.wait_for(task, timeout=1)
