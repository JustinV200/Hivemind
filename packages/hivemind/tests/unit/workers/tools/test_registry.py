"""Unit tests for hivemind.workers.tools.registry: ToolRegistry, ToolInvocation, build_registry.

Roadmap step 10.3: every tool is always offered (a capability decides at invocation, so a refusal
is visible), and `execute` checks `tool:<name>` through the Guard's Enforcer before running one.
"""

from __future__ import annotations

from builders.honey_wire import FakeHoneyChannel
from builders.llm import make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.guard import CapabilitySet
from hivemind.llm import ImagePart, JsonObject, ToolDefinition
from hivemind.pheromone import TrailQuery
from hivemind.workers.tools.errors import ToolError
from hivemind.workers.tools.registry import (
    ToolInvocation,
    ToolOutput,
    ToolRegistry,
    ToolRunner,
    ToolSpec,
    build_registry,
)

_IMAGE = ImagePart(media_type="image/png", data_base64="AAAA")
_SCHEMA: JsonObject = {"type": "object", "properties": {}, "additionalProperties": False}


async def _text_run(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Return plain text, as every tool before roadmap step 6.5 does."""
    return "plain"


async def _media_run(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Return text with an image beside it, as `see` does."""
    return ToolOutput(text="captured", media=(_IMAGE,))


async def _failing_run(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Stop on purpose, as a tool does for an argument it cannot use."""
    raise ToolError("refused on purpose")


def _registry(**runners: ToolRunner) -> ToolRegistry:
    """Build a registry with one tool per runner, named after its keyword."""
    return ToolRegistry(
        ToolSpec(definition=ToolDefinition(name=name, description="d", parameters=_SCHEMA), run=run)
        for name, run in runners.items()
    )


def test_build_registry_offers_every_tool_whatever_the_set_holds() -> None:
    ctx = make_context(capabilities=CapabilitySet.parse())

    registry = build_registry(ctx)

    names = {definition.name for definition in registry.definitions()}
    assert names == {"run_command", "read_file", "write_file", "ask", "keep", "http_request"}


async def test_execute_refuses_a_tool_its_set_does_not_hold_with_the_reason_on_the_trail() -> None:
    ctx = make_context(capabilities=CapabilitySet.parse("tool:read_file", "fs:read:**"))
    registry = build_registry(ctx)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    call = make_tool_call(name="write_file", arguments={"path": "a.txt", "content": "x"})

    result = await registry.execute(invocation, call)

    assert result.text.startswith("refused by the Guard (guard.not_held)")
    [denial] = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "tool_invocation"
    assert denial.payload["capability"] == "tool:write_file"
    # Refused before running: no proposal ever reached the Capping gate.
    assert await ctx.trail.query(TrailQuery(kind="capping.proposed")) == ()


def test_build_registry_offers_recall_and_remember_only_with_a_honey_channel() -> None:
    """Roadmap step 7.8: `tool:*` allows both, but neither is offered until a channel exists."""
    without = build_registry(make_context())
    with_channel = build_registry(make_context(honey=FakeHoneyChannel()))

    assert not {"recall", "remember"} & {tool.name for tool in without.definitions()}
    assert {"recall", "remember"} <= {tool.name for tool in with_channel.definitions()}


async def test_a_honey_tool_its_set_does_not_hold_is_offered_but_refused_by_the_guard() -> None:
    """Roadmap steps 7.8 and 10.3: offered with the channel, refused at `tool_invocation`."""
    ctx = make_context(
        honey=FakeHoneyChannel(),
        capabilities=CapabilitySet.parse("fs:read:**", "exec:*", "tool:recall"),
    )
    registry = build_registry(ctx)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await registry.execute(invocation, make_tool_call(name="remember", arguments={}))

    assert {"recall", "remember"} <= {tool.name for tool in registry.definitions()}
    assert result.text.startswith("refused by the Guard (guard.not_held)")
    [denial] = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["capability"] == "tool:remember"


async def test_execute_refuses_a_call_to_an_unknown_tool_with_a_readable_string() -> None:
    ctx = make_context()
    registry = build_registry(ctx)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    call = make_tool_call(name="not_a_real_tool", arguments={})

    result = await registry.execute(invocation, call)

    assert "no tool named" in result.text


async def test_execute_refuses_an_invalid_argument_with_a_readable_string() -> None:
    ctx = make_context()
    registry = build_registry(ctx)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    # write_file requires "path" and "content"; neither is supplied here.
    call = make_tool_call(name="write_file", arguments={})

    result = await registry.execute(invocation, call)

    assert "path" in result.text and "content" in result.text


def test_tool_invocation_spend_estimate_defaults_to_zero() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    assert invocation.spend_estimate() == 0.0


def test_tool_registry_is_constructible_directly_from_specs() -> None:
    ctx = make_context()
    registry = build_registry(ctx)

    assert isinstance(registry, ToolRegistry)
    assert len(registry.definitions()) >= 4


async def test_execute_wraps_a_runners_plain_text_as_a_text_only_output() -> None:
    ctx = make_context()
    registry = _registry(text_tool=_text_run)

    result = await registry.execute(
        ToolInvocation(ctx=ctx, assignment=make_assignment()), make_tool_call(name="text_tool")
    )

    assert result == ToolOutput(text="plain")


async def test_execute_passes_a_runners_media_through_unchanged() -> None:
    ctx = make_context()
    registry = _registry(media_tool=_media_run)

    result = await registry.execute(
        ToolInvocation(ctx=ctx, assignment=make_assignment()), make_tool_call(name="media_tool")
    )

    assert result.text == "captured"
    assert result.media == (_IMAGE,)


async def test_execute_turns_a_tool_error_into_its_message_with_no_media() -> None:
    ctx = make_context()
    registry = _registry(failing_tool=_failing_run)

    result = await registry.execute(
        ToolInvocation(ctx=ctx, assignment=make_assignment()), make_tool_call(name="failing_tool")
    )

    assert result == ToolOutput(text="refused on purpose")


def test_build_registry_offers_no_exoskeleton_tool_to_a_terminal_only_task() -> None:
    ctx = make_context()

    names = {definition.name for definition in build_registry(ctx).definitions()}

    assert not names & {"see", "click", "browser_navigate", "listen", "say"}
