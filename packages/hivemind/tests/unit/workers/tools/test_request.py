"""Tests for hivemind.workers.tools.comb.request: the request_tool tool.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/comb/request.py (codingrules section 3). The channel is a
    `FakeToolChannel` that records the `ToolRequest` it is handed and answers with a promotion or
    an error; the Guard and the trail are the real ones `make_context` wires.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.comb.request for the module under test.
    - tests/unit/workers/runtime/test_tools.py for the channel itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.comb_tools import (
    FakeToolChannel,
    make_comb_context,
    make_comb_package,
    make_promoted,
    make_warden_id,
)
from builders.workers import make_assignment, run_tool

from hivemind.guard import CapabilitySet
from hivemind.llm import JsonObject
from hivemind.pheromone import TrailQuery
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools import build_registry
from hivemind.workers.tools.comb import request_tool
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import ToolPromoted, ToolRequest, ToolScope

_HOLDS = ("tool:*", "tool:request", "fs:read:**")
_ARGS: JsonObject = {
    "name": "disk_report",
    "description": "Report free disk space in a directory.",
    "required_capabilities": ["fs:read:**"],
    "scope": "cell",
}


def _context(tmp_path: Path, channel: FakeToolChannel, *holds: str) -> WorkerContext:
    return make_comb_context(
        tmp_path, [], tools=channel, capabilities=CapabilitySet.parse(*(holds or _HOLDS))
    )


def _promoted_channel() -> FakeToolChannel:
    return FakeToolChannel(make_promoted(make_comb_package(name="disk_report")), make_warden_id())


async def test_the_request_names_the_asker_the_cell_the_platform_and_the_warden(
    tmp_path: Path,
) -> None:
    channel = _promoted_channel()
    ctx = _context(tmp_path, channel)

    await run_tool(ctx, "request_tool", _ARGS)

    (request,) = channel.requests
    assert (request.name, request.scope) == ("disk_report", ToolScope.CELL)
    assert request.cell_id == ctx.cell.id
    assert request.worker_id == ctx.worker_id
    assert request.warden_id == channel.warden_id
    assert request.platform.os.value == ctx.cell.capabilities.os.value
    assert request.required_capabilities == ("fs:read:**",)
    assert request.description == _ARGS["description"]
    assert request.task_id is not None and request.reason  # the task's own objective by default


async def test_an_explicit_reason_is_sent_as_given(tmp_path: Path) -> None:
    channel = _promoted_channel()
    ctx = _context(tmp_path, channel)

    await run_tool(ctx, "request_tool", {**_ARGS, "reason": "the report is due"})

    assert channel.requests[0].reason == "the report is due"


async def test_a_worker_without_tool_request_is_denied_and_nothing_is_sent(tmp_path: Path) -> None:
    channel = _promoted_channel()
    ctx = _context(tmp_path, channel, "tool:*", "fs:read:**")

    output = await run_tool(ctx, "request_tool", _ARGS)

    assert output.is_error is True and "refused by the Guard" in output.text
    assert channel.requests == []
    denied = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert len(denied) == 1 and "tool:request" in str(denied[0].payload)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"name": "Disk Report"}, "snake_case"),
        ({"description": ""}, "description"),
        ({"scope": "everywhere"}, "scope must be"),
        ({"required_capabilities": "fs:read:**"}, "list of capability strings"),
        ({"required_capabilities": ["nonsense"]}, "not a valid capability"),
        ({"required_capabilities": ["tool:run_command"]}, "never grant tools"),
        ({"required_capabilities": ["net:example.org"]}, "you do not hold"),
    ],
)
async def test_a_malformed_or_over_reaching_request_is_refused_before_it_is_sent(
    tmp_path: Path, overrides: JsonObject, fragment: str
) -> None:
    channel = _promoted_channel()
    ctx = _context(tmp_path, channel)
    invocation = ToolInvocation(ctx, make_assignment(ctx.clock))

    # Straight to the tool, past the registry's schema check: the tool validates for itself.
    output = await request_tool(invocation, {**_ARGS, **overrides})

    assert isinstance(output, ToolOutput)
    assert output.is_error is True and fragment in output.text
    assert channel.requests == []


async def test_a_refusal_from_the_hive_is_reported_with_its_code(tmp_path: Path) -> None:
    refusal = ErrorMessage(
        code="hive.tool.rejected",
        message="The tool failed the Quarantine Comb at the tests gate.",
        failed_kind="tool.request",
        is_retryable=False,
    )
    ctx = _context(tmp_path, FakeToolChannel(refusal, make_warden_id()))

    output = await run_tool(ctx, "request_tool", _ARGS)

    assert output.is_error is True
    assert "hive.tool.rejected" in output.text and "tests gate" in output.text


async def test_a_promotion_the_warden_has_handed_over_is_callable_now(tmp_path: Path) -> None:
    package = make_comb_package(name="disk_report")
    holder: list[WorkerContext] = []

    def answer(_request: ToolRequest) -> ToolPromoted:
        # The Warden re-attenuates the bundle before it forwards the announcement.
        assert holder[0].comb_tools is not None
        holder[0].comb_tools.replace([package.entry])
        return make_promoted(package)

    ctx = _context(tmp_path, FakeToolChannel(answer, make_warden_id()))
    holder.append(ctx)

    output = await run_tool(ctx, "request_tool", _ARGS)

    assert output.is_error is False
    assert output.text == "tool 'disk_report' 1.0.0 is now available; call it by name."


async def test_a_promotion_not_yet_in_the_bundle_says_it_is_not_callable(tmp_path: Path) -> None:
    ctx = _context(tmp_path, _promoted_channel())

    output = await run_tool(ctx, "request_tool", _ARGS)

    assert output.is_error is False
    assert "was promoted" in output.text and "not callable in this attempt" in output.text


async def test_request_tool_without_a_channel_says_the_hive_is_unreachable(
    tmp_path: Path,
) -> None:
    ctx = make_comb_context(tmp_path, [], capabilities=CapabilitySet.parse(*_HOLDS))

    output = await request_tool(ToolInvocation(ctx, make_assignment(ctx.clock)), _ARGS)

    assert isinstance(output, ToolOutput) and "not reachable" in output.text


def test_the_tool_is_offered_only_when_the_runtime_wired_a_channel(tmp_path: Path) -> None:
    with_channel = _context(tmp_path, _promoted_channel())
    without = make_comb_context(tmp_path, [], capabilities=CapabilitySet.parse(*_HOLDS))

    assert "request_tool" in [d.name for d in build_registry(with_channel).definitions()]
    assert "request_tool" not in [d.name for d in build_registry(without).definitions()]
