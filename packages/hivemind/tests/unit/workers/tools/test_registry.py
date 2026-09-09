"""Unit tests for hivemind.workers.tools.registry: ToolRegistry, ToolInvocation, build_registry."""

from __future__ import annotations

from builders.llm import make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.guard import CapabilitySet
from hivemind.workers.tools.registry import ToolInvocation, ToolRegistry, build_registry


def test_build_registry_offers_the_baseline_tools_without_a_net_capability() -> None:
    ctx = make_context()

    registry = build_registry(ctx)

    names = {definition.name for definition in registry.definitions()}
    assert names == {"run_command", "read_file", "write_file", "ask"}


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

    assert "no tool named" in result


async def test_execute_refuses_an_invalid_argument_with_a_readable_string() -> None:
    ctx = make_context()
    registry = build_registry(ctx)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    # write_file requires "path" and "content"; neither is supplied here.
    call = make_tool_call(name="write_file", arguments={})

    result = await registry.execute(invocation, call)

    assert "path" in result and "content" in result


def test_tool_invocation_spend_estimate_defaults_to_zero() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    assert invocation.spend_estimate() == 0.0


def test_tool_registry_is_constructible_directly_from_specs() -> None:
    ctx = make_context()
    registry = build_registry(ctx)

    assert isinstance(registry, ToolRegistry)
    assert len(registry.definitions()) >= 4
