"""Tests for hivemind.workers.roles.drone.outcome.executor: _RecordingExecutor.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/drone/outcome/executor.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.drone.outcome.executor for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.llm import JsonObject, ToolDefinition
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.drone.outcome.executor import (
    MAX_RECORDED_CALLS,
    HandoffRequestedError,
    _RecordingExecutor,
)
from hivemind.workers.tools import ToolInvocation, ToolRegistry, ToolSpec

_STUB_DEFINITION = ToolDefinition(
    name="stub_tool",
    description="A trivial always-succeeding tool, for exercising _RecordingExecutor cheaply.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)


async def _stub_run(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Return a fixed success string; no side effect, so no CappingGate round trip is needed."""
    del invocation, arguments
    return "ok"


def _build_executor(ctx: WorkerContext | None = None) -> _RecordingExecutor:
    active_ctx = ctx if ctx is not None else make_context()
    assignment = make_assignment()
    registry = ToolRegistry([ToolSpec(definition=_STUB_DEFINITION, run=_stub_run)])
    invocation = ToolInvocation(ctx=active_ctx, assignment=assignment)
    return _RecordingExecutor(
        registry, invocation, active_ctx.telemetry, active_ctx.handoff_threshold
    )


async def test_execute_records_a_successful_call() -> None:
    executor = _build_executor()
    call = make_tool_call(name="stub_tool", arguments={})

    result = await executor.execute(call)

    assert result.content == "ok"
    assert result.is_error is False
    assert len(executor.records) == 1
    assert executor.records[0].call == call
    assert executor.records[0].is_error is False


async def test_execute_bounds_records_to_max_recorded_calls() -> None:
    executor = _build_executor()

    for _ in range(MAX_RECORDED_CALLS + 20):
        await executor.execute(make_tool_call(name="stub_tool", arguments={}))

    assert len(executor.records) == MAX_RECORDED_CALLS


async def test_execute_captures_the_pending_call_when_handoff_is_requested() -> None:
    ctx = make_context()
    executor = _build_executor(ctx)
    ctx.telemetry.handoff_requested = True
    call = make_tool_call(name="stub_tool", arguments={})

    with pytest.raises(HandoffRequestedError):
        await executor.execute(call)

    assert executor.pending_call == call
    assert executor.records == []  # Never ran: refused before the registry was ever called.
