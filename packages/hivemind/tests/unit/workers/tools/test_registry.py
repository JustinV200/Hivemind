"""Unit tests for hivemind.workers.tools.registry: ToolRegistry, ToolInvocation, build_registry.

Roadmap step 10.3: every tool is always offered (a capability decides at invocation, so a refusal
is visible), and `execute` checks `tool:<name>` through the Guard's Enforcer before running one.
"""

from __future__ import annotations

from builders.llm import make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.guard import CapabilitySet
from hivemind.pheromone import TrailQuery
from hivemind.workers.tools.registry import ToolInvocation, ToolRegistry, build_registry


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

    assert result.startswith("refused by the Guard (guard.not_held)")
    [denial] = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "tool_invocation"
    assert denial.payload["capability"] == "tool:write_file"
    # Refused before running: no proposal ever reached the Capping gate.
    assert await ctx.trail.query(TrailQuery(kind="capping.proposed")) == ()


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
