"""Tests for hivemind.workers.runtime.loop.WorkerRuntime's Honey wiring (roadmap step 7.8).

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/loop.py and .attempt (codingrules section 3); split by
    feature (14.2) from test_loop.py. A real `WorkerRuntime` runs as a background task over a
    MemoryTransport pair; the Warden's end is read raw (`builders.honey_wire.WireEnd`), because
    the Honey kinds are exactly what this module watches for.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.loop for the runtime's `ctx.honey` and HoneyResponse dispatch.
    - hivemind.workers.runtime.attempt for the Handoff deposit after a checkpoint.
"""

from __future__ import annotations

import asyncio

from builders.honey_wire import WireEnd, make_honey_query
from builders.memory import make_handoff
from builders.workers import RunScript, ScriptedWorker, make_assignment, make_context, make_outcome

from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.runtime.honey import MailboxHoneyChannel
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop
from waggle.ids import new_node_id, new_warden_id, new_worker_id
from waggle.messages.base import WaggleMessage
from waggle.messages.honey import HoneyQuery, HoneyResponse, NectarDeposit, NectarKind
from waggle.messages.task import TaskAssign, TaskProgress, TaskResult, TaskStage
from waggle.transport.memory import MemoryTransport

_PUMP_LIMIT = 20  # Envelopes to read before giving up on the one a test waits for.


def _build(clock: FakeClock, script: RunScript) -> tuple[WorkerRuntime, WireEnd]:
    """Wire a WorkerRuntime running `script` to a raw Warden end over a fresh pair."""
    worker_id, warden_id, node_id = new_worker_id(clock), new_warden_id(clock), new_node_id(clock)
    warden_transport, worker_transport = MemoryTransport.pair(Codec(), Codec())
    deps = RuntimeDeps(
        transport=worker_transport,
        hop=Hop(sender=worker_id, recipient=warden_id, node_id=node_id),
        heartbeat_interval_s=5.0,
        clock=clock,
    )
    runtime = WorkerRuntime(
        make_context(clock=clock, worker_id=worker_id), ScriptedWorker(script), deps
    )
    warden_hop = Hop(sender=warden_id, recipient=worker_id, node_id=node_id)
    return runtime, WireEnd(warden_transport, warden_hop, clock)


async def _next_of[MessageT: WaggleMessage](
    warden: WireEnd, kind: type[MessageT]
) -> tuple[Envelope, MessageT]:
    """Read envelopes until one carries a `kind` payload; return it and its payload."""
    for _ in range(_PUMP_LIMIT):
        envelope = await warden.next()
        if isinstance(envelope.payload, kind):
            return envelope, envelope.payload
    raise AssertionError(f"No {kind.__name__} within {_PUMP_LIMIT} envelopes.")


async def test_the_runtime_gives_the_role_its_own_honey_channel_and_resolves_the_answer() -> None:
    clock = FakeClock()
    seen: list[WorkerContext] = []

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        seen.append(ctx)
        assert ctx.honey is not None
        query = make_honey_query(clock, requester=ctx.worker_id, task_id=assignment.task_id)
        response = await ctx.honey.query(query)
        return make_outcome(summary=response.reason)

    runtime, warden = _build(clock, script)
    run_task = asyncio.ensure_future(runtime.run())
    await warden.send(make_assignment(clock=clock))
    asked, query = await _next_of(warden, HoneyQuery)
    answer = HoneyResponse(
        hits=(), token_count=0, is_truncated=False, filtered_count=0, reason="Nothing yet."
    )
    await warden.send(answer, correlation_id=asked.id)
    _, result = await _next_of(warden, TaskResult)

    runtime.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    assert isinstance(seen[0].honey, MailboxHoneyChannel)
    assert query.requester == seen[0].worker_id
    assert result.summary == "Nothing yet."


async def test_a_checkpoint_deposits_its_handoff_as_nectar_keyed_by_its_event() -> None:
    clock = FakeClock()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        await asyncio.sleep(0)
        if resume_from is None:
            # First attempt: hand off, so the runtime checkpoints and restarts the role.
            return make_outcome(claimed=False, handoff=make_handoff())
        return make_outcome()

    runtime, warden = _build(clock, script)
    run_task = asyncio.ensure_future(runtime.run())
    assignment = make_assignment(clock=clock)
    await warden.send(assignment)
    progress = await _checkpointed(warden)
    _, deposit = await _next_of(warden, NectarDeposit)
    await _next_of(warden, TaskResult)

    runtime.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    assert progress.handoff is not None
    assert deposit.kind is NectarKind.HANDOFF
    assert deposit.event_id == progress.handoff.event_id
    assert deposit.task_id == assignment.task_id
    assert deposit.final is True


async def _checkpointed(warden: WireEnd) -> TaskProgress:
    """Read envelopes until the CHECKPOINTED TaskProgress arrives."""
    for _ in range(_PUMP_LIMIT):
        _, progress = await _next_of(warden, TaskProgress)
        if progress.stage is TaskStage.CHECKPOINTED:
            return progress
    raise AssertionError("No CHECKPOINTED progress arrived.")
