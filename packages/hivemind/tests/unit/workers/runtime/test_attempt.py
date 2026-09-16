"""Tests for hivemind.workers.runtime.attempt: crash-to-Alarm classification.

Roadmap step 4.10's own wiring-pass deliverable: "A Drone whose overflow retries are exhausted
raises an Alarm of kind CONTEXT_OVERFLOW" -- landed in a prior dispatch
(`hivemind.workers.runtime.attempt._CRASH_ALARM_KINDS` already maps `ContextOverflowError`), this
module is the test that proves it through a real `WorkerRuntime`, the same "drive a real runtime
over an in-process transport pair" shape `test_loop.py`'s own
`test_a_raising_role_produces_an_alarm_moves_to_failed_and_the_loop_keeps_running` uses for a
plain `RuntimeError` (`AlarmKind.WORKER_CRASHED`). Split into its own module, rather than added to
`test_loop.py`, because that file is already at its own 400-line test-file cap (codingrules 5.1).

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/attempt.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.attempt for AttemptManager and `_CRASH_ALARM_KINDS`, the module
      under test.
    - tests.unit.workers.runtime.test_loop for the sibling crash-to-Alarm test this one mirrors.
"""

from __future__ import annotations

import asyncio

from builders.workers import RunScript, ScriptedWorker, WardenEnd, make_assignment, make_context

from hivemind.memory import Handoff
from hivemind.memory.overflow import MAX_OVERFLOWS, ContextOverflowError
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_node_id, new_warden_id, new_worker_id
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign


def _build(clock: FakeClock, script: RunScript) -> tuple[WorkerRuntime, WardenEnd]:
    """Wire a WorkerRuntime to a WardenEnd over a fresh MemoryTransport pair.

    Mirrors `tests.unit.workers.runtime.test_loop._build`'s own shape (that helper is
    module-private, so this file builds its own rather than reaching across a test module
    boundary); trimmed to the two return values this module's one test actually needs.
    """
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
    runtime = WorkerRuntime(ctx, ScriptedWorker(script), deps)
    return runtime, warden_end


async def test_exhausted_overflow_retries_raise_an_alarm_of_kind_context_overflow() -> None:
    """`_CRASH_ALARM_KINDS` maps `ContextOverflowError` onto `AlarmKind.CONTEXT_OVERFLOW`.

    `hivemind.memory.overflow.run_with_overflow_retry` raises `ContextOverflowError` once a role's
    own overflow retries are exhausted (roadmap step 4.4); a Drone lets it propagate through its
    own tool loop and out of `Worker.run` (module docstring), where `AttemptManager.on_finished`
    catches it the same way it catches any other crashed role's own exception.
    """
    clock = FakeClock()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        raise ContextOverflowError(attempts=MAX_OVERFLOWS)

    runtime, warden_end = _build(clock, script)
    task = asyncio.create_task(runtime.run())

    await warden_end.send(make_assignment(clock=clock))
    alarm = await warden_end.wait_for_alarm()

    assert alarm.kind is AlarmKind.CONTEXT_OVERFLOW
    assert runtime.state is WorkerState.FAILED

    runtime.stop()
    await asyncio.wait_for(task, timeout=1)
