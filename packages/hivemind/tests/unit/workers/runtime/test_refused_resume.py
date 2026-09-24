"""Tests for hivemind.workers.runtime: a Handoff the loader refuses is never resumed from.

Roadmap step 10.6d: every Handoff loader refuses a tainted Handoff outright. When a TaskAssign
asks a Worker to resume from one, the runtime fails the attempt with an Alarm before the role ever
starts, so the Warden's escalation policy decides what follows, instead of the refusal ending the
runtime.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/loop.py and attempt.py (codingrules section 3), split by
    feature from test_loop.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.checkpoint.read_handoff for the refusal.
    - hivemind.workers.runtime.attempt.AttemptManager.refuse_resume for the failure it becomes.
"""

from __future__ import annotations

import asyncio

from builders.memory import make_handoff
from builders.taint import make_stamp
from builders.workers import (
    ScriptedWorker,
    WardenEnd,
    make_assignment,
    make_context,
    make_outcome,
)

from hivemind.memory import Handoff, MemoryContext, write_checkpoint
from hivemind.memory.taint import TaintedKind, TaintScope, taint_memory
from hivemind.pheromone import TrailQuery
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_node_id, new_warden_id, new_worker_id
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign


async def test_resuming_from_a_tainted_handoff_fails_with_an_alarm_and_never_runs_the_role() -> (
    None
):
    clock = FakeClock()
    worker_id, warden_id, node_id = new_worker_id(clock), new_warden_id(clock), new_node_id(clock)
    warden_end, transport = WardenEnd.pair_with(worker_id, warden_id, node_id, clock)
    ctx = make_context(clock=clock, worker_id=worker_id)
    memory = MemoryContext(store=ctx.memory, identity=ctx.identity, clock=clock)
    ref = await write_checkpoint(make_handoff(written_by=worker_id), None, memory)
    scope = TaintScope(
        authors=frozenset({worker_id}), since=clock.now(), kinds=frozenset({TaintedKind.HANDOFF})
    )
    await taint_memory(scope, make_stamp(), memory)
    ran: list[bool] = []

    async def script(
        _ctx: WorkerContext, _assign: TaskAssign, _resume: Handoff | None
    ) -> WorkerOutcome:
        ran.append(True)
        return make_outcome()

    deps = RuntimeDeps(
        transport=transport,
        hop=Hop(sender=worker_id, recipient=warden_id, node_id=node_id),
        heartbeat_interval_s=5.0,
        clock=clock,
    )
    runtime = WorkerRuntime(ctx, ScriptedWorker(script), deps)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock, resume_from=ref))
    alarm = await warden_end.wait_for_alarm()

    assert alarm.kind is AlarmKind.WORKER_CRASHED
    assert "tainted" in alarm.detail and ref.event_id in alarm.detail
    assert runtime.state is WorkerState.FAILED and ran == []
    kinds = [event.kind for event in await ctx.trail.query(TrailQuery(subject_id=worker_id))]
    assert kinds == ["worker.started", "worker.failed"]
    runtime.stop()
    await asyncio.wait_for(task, timeout=1)
