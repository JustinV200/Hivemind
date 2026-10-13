"""Tests for the bounded loop's pickup of a changed Comb tool bundle (roadmap step 9.7).

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/bounded_loop/runner.py and executor.py (codingrules
    section 3); split by feature (14.2) from the Drone's own tests. A real Drone runs the real loop
    over a scripted `FakeLLMProvider`; the Warden's part (re-attenuating the Worker's bundle) is a
    callback on a fake tool channel.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.bounded_loop.runner for `run_bounded_loop`'s tool rebuilds.
    - hivemind.workers.roles.bounded_loop.executor for the `ToolsChangedError` raise.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from pathlib import Path

import pytest
from builders.comb_tools import (
    CombPackage,
    FakePackageSource,
    FakeToolChannel,
    make_comb_context,
    make_comb_package,
    make_promoted,
    make_warden_id,
)
from builders.llm import make_bound, make_tool_call
from builders.workers import make_assignment

from hivemind.guard import CapabilitySet
from hivemind.llm import (
    FakeLLMProvider,
    LLMRequest,
    ToolResultPart,
    text_response,
    tool_call_response,
)
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.bounded_loop.executor import LoopExecutor
from hivemind.workers.roles.bounded_loop.runner import MAX_TOOL_REBUILDS
from hivemind.workers.roles.drone import Drone
from hivemind.workers.tools import ToolInvocation, ToolsChangedError, build_registry
from waggle.messages.tool.authoring import ToolPromoted, ToolRequest

_HOLDS = ("tool:*", "tool:request", "fs:read:**", "fs:write:scratch/**", "exec:*")
_REQUEST = {
    "name": "echo_tool",
    "description": "Echo the arguments back.",
    "required_capabilities": [],
    "scope": "cell",
}


def _rig(
    tmp_path: Path, provider: FakeLLMProvider, package: CombPackage
) -> tuple[WorkerContext, FakeToolChannel]:
    """A Worker with the request_tool channel; answering a request hands it `package`."""
    holder: list[WorkerContext] = []

    def answer(_request: ToolRequest) -> ToolPromoted:
        ctx = holder[0]
        assert ctx.comb_tools is not None and isinstance(ctx.package_source, FakePackageSource)
        ctx.package_source.add(package)
        ctx.comb_tools.replace([package.entry])  # the Warden re-attenuates, then forwards
        return make_promoted(package)

    channel = FakeToolChannel(answer, make_warden_id())
    ctx = make_comb_context(
        tmp_path,
        [],
        tools=channel,
        capabilities=CapabilitySet.parse(*_HOLDS),
        bound=make_bound(provider=provider),
    )
    holder.append(ctx)
    return ctx, channel


def _offered(request: LLMRequest) -> set[str]:
    return {tool.name for tool in request.tools}


def _tool_results(request: LLMRequest) -> list[object]:
    """The JSON values of every tool result in the request's conversation."""
    values: list[object] = []
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                with contextlib.suppress(ValueError):
                    values.append(json.loads(part.content))
    return values


async def test_a_requested_tool_is_offered_after_the_loop_restarts_and_can_be_called(
    tmp_path: Path,
) -> None:
    provider = FakeLLMProvider()
    package = make_comb_package(name="echo_tool")
    ctx, channel = _rig(tmp_path, provider, package)
    provider.script(
        tool_call_response(make_tool_call(id="c1", name="request_tool", arguments=_REQUEST)),
        tool_call_response(make_tool_call(id="c2", name="echo_tool", arguments={"text": "hi"})),
        text_response("Used the new tool."),
    )

    outcome = await Drone().run(ctx, make_assignment(ctx.clock), resume_from=None)

    assert outcome.claimed is True
    assert len(channel.requests) == 1
    first, second, third = provider.calls
    assert "echo_tool" not in _offered(first) and "request_tool" in _offered(first)
    assert "echo_tool" in _offered(second)  # the restarted loop is offered the new tool
    # The restarted episode is told, from state, what changed (a hot-state note).
    assert "Your tool set changed" in (second.system or "")
    assert "echo_tool" in (second.system or "")
    assert {"echo": {"text": "hi"}} in _tool_results(third)  # the new tool really ran


async def test_work_done_before_the_restart_is_kept_in_the_outcome(tmp_path: Path) -> None:
    provider = FakeLLMProvider()
    ctx, _channel = _rig(tmp_path, provider, make_comb_package(name="echo_tool"))
    provider.script(
        tool_call_response(
            make_tool_call(
                id="c1", name="write_file", arguments={"path": "notes.txt", "content": "a"}
            )
        ),
        tool_call_response(make_tool_call(id="c2", name="request_tool", arguments=_REQUEST)),
        text_response("Done."),
    )

    outcome = await Drone().run(ctx, make_assignment(ctx.clock), resume_from=None)

    assert {artifact.path for artifact in outcome.artifacts} == {"notes.txt"}


async def test_usage_from_every_loop_is_recorded_not_just_the_last(tmp_path: Path) -> None:
    provider = FakeLLMProvider()
    ctx, _channel = _rig(tmp_path, provider, make_comb_package(name="echo_tool"))
    provider.script(
        tool_call_response(make_tool_call(id="c1", name="request_tool", arguments=_REQUEST)),
        text_response("Done."),
    )

    await Drone().run(ctx, make_assignment(ctx.clock), resume_from=None)

    assert len(provider.calls) == 2  # both loops called the model through the tally


async def test_a_warden_that_keeps_changing_the_bundle_cannot_loop_the_attempt_forever(
    tmp_path: Path,
) -> None:
    provider = FakeLLMProvider()
    ctx, _channel = _rig(tmp_path, provider, make_comb_package(name="echo_tool"))
    assert ctx.comb_tools is not None
    bundle = ctx.comb_tools
    flips = iter(make_comb_package(name=f"tool_{n}") for n in range(MAX_TOOL_REBUILDS + 3))

    def answer(_request: ToolRequest) -> ToolPromoted:
        chosen = next(flips)  # every request flips the bundle to a different entry set
        bundle.replace([chosen.entry])
        return make_promoted(chosen)

    flipping = dataclasses.replace(ctx, tools=FakeToolChannel(answer, make_warden_id()))
    provider.script(
        *[
            tool_call_response(make_tool_call(id=f"c{n}", name="request_tool", arguments=_REQUEST))
            for n in range(MAX_TOOL_REBUILDS + 3)
        ]
    )

    with pytest.raises(ToolsChangedError):
        await Drone().run(flipping, make_assignment(ctx.clock), resume_from=None)

    # One loop to start with, then MAX_TOOL_REBUILDS restarts, each cut short by its first call.
    assert len(provider.calls) == MAX_TOOL_REBUILDS + 1


# ──────────────────────────────────────────────────────────────────────────────
# The executor on its own
# ──────────────────────────────────────────────────────────────────────────────


def _executor(ctx: WorkerContext) -> LoopExecutor:
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(ctx.clock))
    return LoopExecutor(build_registry(ctx), invocation, ctx.telemetry, ctx.handoff_threshold)


async def test_the_executor_refuses_a_call_decided_against_a_stale_tool_menu(
    tmp_path: Path,
) -> None:
    ctx = make_comb_context(tmp_path, [])
    executor = _executor(ctx)
    assert ctx.comb_tools is not None
    ctx.comb_tools.replace([make_comb_package(name="echo_tool").entry])  # changed while it thought

    with pytest.raises(ToolsChangedError):
        await executor.execute(make_tool_call(name="read_file", arguments={"path": "x"}))
    assert executor.records == []  # the call never ran


async def test_use_registry_moves_the_executor_to_the_current_version(tmp_path: Path) -> None:
    ctx = make_comb_context(tmp_path, [])
    executor = _executor(ctx)
    assert ctx.comb_tools is not None
    ctx.comb_tools.replace([make_comb_package(name="echo_tool").entry])
    assert isinstance(ctx.package_source, FakePackageSource)
    ctx.package_source.add(make_comb_package(name="echo_tool"))

    executor.use_registry(build_registry(ctx))
    result = await executor.execute(make_tool_call(name="ask", arguments={}))

    assert result.call_id  # no ToolsChangedError for a change the registry already holds
    assert "echo_tool" in [d.name for d in executor.registry.definitions()]


async def test_a_context_without_a_bundle_never_raises_tools_changed(tmp_path: Path) -> None:
    ctx = make_comb_context(tmp_path, [], comb_tools=None)
    executor = _executor(ctx)

    result = await executor.execute(make_tool_call(name="no_such_tool", arguments={}))

    assert result.is_error is True
