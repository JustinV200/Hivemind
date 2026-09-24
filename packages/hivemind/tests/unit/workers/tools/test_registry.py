"""Unit tests for hivemind.workers.tools.registry: ToolRegistry, ToolInvocation, build_registry."""

from __future__ import annotations

from builders.llm import make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.guard import CapabilitySet
from hivemind.llm import ImagePart, JsonObject, ToolDefinition
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


def test_build_registry_offers_the_baseline_tools_without_a_net_capability() -> None:
    ctx = make_context()

    registry = build_registry(ctx)

    names = {definition.name for definition in registry.definitions()}
    assert names == {"run_command", "read_file", "write_file", "ask", "keep"}


def test_build_registry_adds_http_request_when_a_net_capability_is_held() -> None:
    ctx = make_context(capabilities=CapabilitySet.parse("net:example.com"))

    registry = build_registry(ctx)

    names = {definition.name for definition in registry.definitions()}
    assert "http_request" in names


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
