"""Shutdown-hygiene tests for hivemind.workers.runtime.loop.WorkerRuntime: stop() leaks no task.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/loop.py (codingrules section 3); split by feature (14.2)
    from test_loop.py, which already covers the rest of WorkerRuntime's own lifecycle. Each test
    here compares `asyncio.all_tasks()` before spawning `runtime.run()` against the set after
    `stop()` returns and that task is awaited, proving no throwaway or owned task this runtime
    started is ever left pending once `run()` ends (this dispatch's own shutdown-hygiene pass).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.loop for WorkerRuntime, the module under test.
    - hivemind.workers.runtime.attempt for AttemptManager.cancel_role_task, this dispatch's own
      fix the second test here proves.
"""

from __future__ import annotations

import asyncio

from builders.workers import (
    RunScript,
    ScriptedWorker,
    WardenEnd,
    make_assignment,
    make_context,
    yield_then,
)
from builders.workers import make_outcome as _make_outcome

from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_node_id, new_warden_id, new_worker_id
from waggle.messages.task import TaskAssign, TaskStage


def _build(clock: FakeClock, script: RunScript) -> tuple[WorkerRuntime, WardenEnd]:
    """Wire a WorkerRuntime running `script` to a WardenEnd (mirrors test_loop.py's `_build`)."""
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
    return WorkerRuntime(ctx, ScriptedWorker(script), deps), warden_end


async def test_stop_leaves_no_pending_tasks_behind_with_no_active_attempt() -> None:
    """This dispatch's own shutdown-hygiene proof: `stop()` reaps every task it owns."""
    clock = FakeClock()
    runtime, _warden_end = _build(clock, yield_then(_make_outcome))
    before = asyncio.all_tasks() - {asyncio.current_task()}
    task = asyncio.ensure_future(runtime.run())
    await asyncio.sleep(0)  # Let the first tick start its own receive/heartbeat tasks.

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)

    after = asyncio.all_tasks() - {asyncio.current_task()}
    assert after == before


async def test_stop_reaps_a_role_still_running_mid_attempt() -> None:
    """This dispatch's own fix: a role stuck mid-attempt is cancelled, not left running unowned.

    Before this fix, `_tick`'s own stop branch closed the mailbox and returned without ever
    touching `AttemptManager.role_task`: a role blocked on a tool call or an unanswered `ask()`
    kept running with no owner once `run()` had already ended.
    """
    clock = FakeClock()
    started = asyncio.Event()
    forever = asyncio.Event()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        started.set()
        await forever.wait()  # Never set; this attempt only ends by being cancelled.
        return _make_outcome()  # pragma: no cover

    runtime, warden_end = _build(clock, script)
    before = asyncio.all_tasks() - {asyncio.current_task()}
    task = asyncio.ensure_future(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    await warden_end.wait_for_progress(TaskStage.STARTED)
    await started.wait()

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)

    after = asyncio.all_tasks() - {asyncio.current_task()}
    assert after == before
