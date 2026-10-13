"""Tests for hivemind.workers.runtime.loop.WorkerRuntime's Tool channel wiring (roadmap step 9.7).

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/loop.py (codingrules section 3); split by feature (14.2)
    from test_loop.py. A real `WorkerRuntime` runs as a background task over a MemoryTransport
    pair, with the Warden's end read raw.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.loop for the runtime's `ctx.tools` and its reply dispatch.
    - test_tools.py for the channel on its own.
"""

from __future__ import annotations

import asyncio

from builders.comb_tools import make_comb_package, make_promoted
from builders.honey_wire import WireEnd
from builders.royal_jelly import make_tool_request
from builders.workers import RunScript, ScriptedWorker, make_assignment, make_context, make_outcome

from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.runtime.tools import MailboxToolChannel
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop
from waggle.ids import new_node_id, new_warden_id, new_worker_id
from waggle.messages.control import ErrorMessage
from waggle.messages.task import TaskAssign, TaskResult
from waggle.messages.tool.authoring import ToolPromoted, ToolRequest
from waggle.transport.memory import MemoryTransport

_PUMP_LIMIT = 20  # Envelopes to read before giving up on the one a test waits for.


def _build(clock: FakeClock, script: RunScript) -> tuple[WorkerRuntime, WireEnd, str]:
    """Wire a WorkerRuntime running `script` to a raw Warden end; also return the Warden's id."""
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
    return runtime, WireEnd(warden_transport, warden_hop, clock), str(warden_id)


async def _next_request(warden: WireEnd) -> tuple[Envelope, ToolRequest]:
    for _ in range(_PUMP_LIMIT):
        envelope = await warden.next()
        if isinstance(envelope.payload, ToolRequest):
            return envelope, envelope.payload
    raise AssertionError(f"No ToolRequest within {_PUMP_LIMIT} envelopes.")


async def _finish(runtime: WorkerRuntime, warden: WireEnd, run_task: asyncio.Task[None]) -> None:
    """Read until the Worker reports its result, then stop the runtime and wait for it."""
    for _ in range(_PUMP_LIMIT):
        if isinstance((await warden.next()).payload, TaskResult):
            break
    runtime.stop()
    await asyncio.wait_for(run_task, timeout=5.0)


async def test_the_runtime_gives_the_role_a_tool_channel_that_names_the_warden() -> None:
    clock = FakeClock()
    seen: list[WorkerContext] = []

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        seen.append(ctx)
        return make_outcome()

    runtime, warden, warden_id = _build(clock, script)
    run_task = asyncio.ensure_future(runtime.run())
    await warden.send(make_assignment(clock=clock))
    await _finish(runtime, warden, run_task)

    assert isinstance(seen[0].tools, MailboxToolChannel)
    assert str(seen[0].tools.warden_id) == warden_id


async def test_a_promotion_forwarded_by_the_warden_resolves_the_roles_request() -> None:
    clock = FakeClock()
    answers: list[object] = []

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        assert ctx.tools is not None
        request = make_tool_request(
            clock,
            warden_id=ctx.tools.warden_id,
            worker_id=ctx.worker_id,
            task_id=assignment.task_id,
        )
        answers.append(await ctx.tools.request(request))
        return make_outcome()

    runtime, warden, _warden_id = _build(clock, script)
    run_task = asyncio.ensure_future(runtime.run())
    await warden.send(make_assignment(clock=clock))
    _envelope, request = await _next_request(warden)
    promoted = make_promoted(make_comb_package(name=request.name)).model_copy(
        update={"tool_id": request.tool_id}
    )
    await warden.send(promoted)
    await _finish(runtime, warden, run_task)

    assert answers == [promoted] and isinstance(answers[0], ToolPromoted)


async def test_an_error_correlated_to_the_request_resolves_it_as_a_refusal() -> None:
    clock = FakeClock()
    answers: list[object] = []

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        assert ctx.tools is not None
        request = make_tool_request(clock, warden_id=ctx.tools.warden_id)
        answers.append(await ctx.tools.request(request))
        return make_outcome()

    runtime, warden, _warden_id = _build(clock, script)
    run_task = asyncio.ensure_future(runtime.run())
    await warden.send(make_assignment(clock=clock))
    envelope, _request = await _next_request(warden)
    refusal = ErrorMessage(
        code="hive.tool.rejected",
        message="Refused.",
        failed_kind="tool.request",
        is_retryable=False,
    )
    await warden.send(refusal, correlation_id=envelope.id)
    await _finish(runtime, warden, run_task)

    assert answers == [refusal]
