"""Tests for hivemind.workers.runtime: a Handoff the store does not hold starts the attempt fresh.

The Queen sends a Warden that cannot read her memory store the reference to the Handoff alone when
she holds no copy, for it to read from its own store; one that was not the Warden to write it finds
nothing there. That is no refusal (nothing tainted it, nothing is above the clearance): there is
only nothing to resume from, so the attempt starts fresh, as ADR-0024 says of a Handoff that
cannot be read. Before, the loader's `HandoffNotFoundError` escaped `begin_attempt` and ended the
runtime, with the task RUNNING and no bee.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/attempt.py (codingrules section 3), split by feature from
    test_attempt.py and test_refused_resume.py, the refusing half.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.attempt.begin_attempt for the start under test.
    - tests.unit.queen.dispatcher.test_ready_handoff for the Queen sending the reference alone.
"""

from __future__ import annotations

import asyncio

from builders.workers import (
    ScriptedWorker,
    WardenEnd,
    make_assignment,
    make_context,
    make_outcome,
)
from structlog.testing import capture_logs

from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.envelope import Hop
from waggle.ids import new_event_id, new_node_id, new_warden_id, new_worker_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskAssign, TaskOutcome, TaskStage

_WAIT_S = 5.0  # How long a scenario waits for the runtime to answer before it fails.


async def test_resuming_from_a_handoff_the_store_does_not_hold_starts_the_role_fresh() -> None:
    clock = FakeClock()
    worker_id, warden_id, node_id = new_worker_id(clock), new_warden_id(clock), new_node_id(clock)
    warden_end, transport = WardenEnd.pair_with(worker_id, warden_id, node_id, clock)
    ctx = make_context(clock=clock, worker_id=worker_id)  # A store that has never seen the ref.
    unheld = HandoffRef(
        event_id=new_event_id(clock), written_at=clock.now(), clearance=WireHoneyClearance.C1
    )
    resumed_from: list[Handoff | None] = []

    async def script(
        _ctx: WorkerContext, _assign: TaskAssign, resume: Handoff | None
    ) -> WorkerOutcome:
        resumed_from.append(resume)
        return make_outcome(summary="started over")

    deps = RuntimeDeps(
        transport=transport,
        hop=Hop(sender=worker_id, recipient=warden_id, node_id=node_id),
        heartbeat_interval_s=3_600.0,
        clock=clock,
    )
    runtime = WorkerRuntime(ctx, ScriptedWorker(script), deps)
    loop = asyncio.create_task(runtime.run())

    with capture_logs() as logs:
        await warden_end.send(make_assignment(clock=clock, resume_from=unheld))
        started = await asyncio.wait_for(warden_end.wait_for_progress(TaskStage.STARTED), _WAIT_S)
        result = await asyncio.wait_for(warden_end.wait_for_result(), _WAIT_S)

    # The role ran, from nothing, and finished: no Alarm, no dead runtime.
    assert resumed_from == [None]
    assert result.outcome is TaskOutcome.CLAIMED and warden_end.alarms == []
    assert runtime.state is WorkerState.DONE and not loop.done()
    # It says so: on the progress the Warden reads, and in the log with the ids it asked for.
    assert "fresh" in started.summary
    [seen] = [entry for entry in logs if entry["event"] == "workers.runtime.handoff_not_held"]
    assert seen["event_id"] == unheld.event_id and seen["worker_id"] == worker_id
    runtime.stop()
    await asyncio.wait_for(loop, timeout=_WAIT_S)
