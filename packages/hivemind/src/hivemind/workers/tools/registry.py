"""Define ToolInvocation, ToolSpec and ToolRegistry: how a Drone's tools are offered and run.

`hivemind.workers.roles.drone.Drone` builds one `ToolRegistry` per attempt from `build_registry`
and hands its `definitions()` to `hivemind.llm.run_tool_loop` as the tools a model may call; the
ladder validates a call's schema itself before ever invoking a `ToolExecutor`, but this registry
validates again on `execute` (codingrules section 15: "Tool calls proposed by a model are validated
against the tool's schema and the Worker's capabilities before execution" -- true whichever caller
reaches `ToolRegistry.execute` directly, not only through the ladder). `ToolInvocation` is the one
bundle every `ToolRunner` receives: a Worker's tools need the current `TaskAssign` (for its task
id, tempo and clearance, when they build a Capping `Proposal`) as well as `WorkerContext`, and
codingrules section 8.7's "never a provider, a subprocess handle" pattern for `WorkerContext`
itself argues against stashing one task's assignment onto that shared value, so it travels
alongside instead.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Built and read by
    `hivemind.workers.roles.drone.Drone`; the five `ToolSpec`s it registers live in
    `hivemind.workers.tools.session`, `.http` and `.ask`. Calls into `hivemind.guard`,
    `hivemind.llm`, `hivemind.workers.context`, `hivemind.workers.tools.errors` and waggle only.

Key invariants:
    - `ToolRegistry.execute` never raises for an unknown tool or an invalid argument: both become
      an error string a model can read (codingrules section 15). A `ToolError` a runner raises on
      purpose becomes its message the same way; every other exception -- a control exception such
      as `hivemind.workers.roles.drone.HandoffRequestedError` or
      `hivemind.workers.errors.WorkerCancelledError` included -- propagates unchanged.
    - `build_registry` offers `http_request` only when `ctx.capabilities` holds at least one `net`
      capability; offering a tool with nothing it could ever be allowed to do would only invite a
      model to try it and be refused every time.

See Also:
    - .claude/codingrules.md section 15 for "LLM output is untrusted input" and least privilege.
    - .claude/codingrules.md section 8.4 for "plugins by registry, not by if chain", the pattern
      `ToolRegistry` follows for a Drone's own tool set.
    - hivemind.workers.roles.drone for Drone, this registry's one builder and caller.
    - hivemind.workers.tools.session, .http and .ask for the concrete ToolSpecs this module wires.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from hivemind.guard import CapabilityFamily
from hivemind.llm import JsonObject, ToolCall, ToolDefinition, validate_arguments
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.errors import ToolError
from waggle.messages.task import TaskAssign

__all__ = ["ToolInvocation", "ToolRegistry", "ToolRunner", "ToolSpec", "build_registry"]


class ToolRunner(Protocol):
    """Run one validated tool call and return its result as text."""

    async def __call__(self, invocation: ToolInvocation, arguments: JsonObject) -> str:
        """Run this tool with `arguments` and return the text a model should see.

        Args:
            invocation: This attempt's WorkerContext and TaskAssign.
            arguments: The call's arguments, already validated against this tool's schema.

        Returns:
            The tool-result text: what happened, in a form a model can act on next turn.

        Raises:
            hivemind.workers.tools.errors.ToolError: Something the caller cannot recover from by
                trying different arguments (`hivemind.workers.tools.registry.ToolRegistry.execute`
                turns this into its message).
        """
        ...


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool this registry can offer: its schema, and how to run it."""

    definition: ToolDefinition  # Name, description and JSON-schema parameters (hivemind.llm).
    run: ToolRunner  # How to actually run a validated call.


def _zero_spend_estimate() -> float:
    """Report a spend estimate of 0.0: every v0 tool proposal costs a fixed 0.0 to estimate.

    Defined ahead of `ToolInvocation` below, out of the usual private-helpers-last order
    (codingrules section 5.3), because a dataclass field's `default=` is evaluated once at class
    body execution time and so needs this name to already exist.
    """
    return 0.0


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Everything one tool call needs beyond its own arguments: the context and the assignment.

    Built once per `hivemind.workers.roles.drone.Drone.run` attempt and passed to every
    `ToolRegistry.execute` call that attempt makes.

    Attributes:
        ctx: This attempt's WorkerContext: the Cell, session, capabilities, Capping gate and lease
            every tool reads and proposes through.
        assignment: The TaskAssign this attempt is working: its task id, tempo and clearance stamp
            every Proposal and Question a tool builds.
        spend_estimate: Reports this attempt's running spend estimate in USD; every v0 tool's own
            proposals cost a fixed 0.0 (hivemind.workers.tools.proposals), so nothing here reads
            it yet, but the seam exists for the tool that first needs to check remaining budget
            before proposing something with a real cost.
    """

    ctx: WorkerContext
    assignment: TaskAssign
    # Every v0 tool proposal's own spend estimate is a fixed 0.0 (hivemind.workers.tools.
    # proposals), so nothing reads this yet; the default keeps a test's ToolInvocation buildable
    # without supplying a callback of its own.
    spend_estimate: Callable[[], float] = field(default=_zero_spend_estimate)


class ToolRegistry:
    """Every tool one Drone attempt may call: its schemas, and how to validate and run a call."""

    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        """Build a registry over `specs`, keyed by each one's own tool name.

        Args:
            specs: The tools to offer; `build_registry` is the usual way to build this iterable.
        """
        self._specs = {spec.definition.name: spec for spec in specs}

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return every offered tool's definition, for `hivemind.llm.LLMRequest.tools`.

        Returns:
            One ToolDefinition per registered tool, in registration order.
        """
        return tuple(spec.definition for spec in self._specs.values())

    async def execute(self, invocation: ToolInvocation, call: ToolCall) -> str:
        """Validate and run `call`, returning tool-result text a model can read.

        Args:
            invocation: This attempt's context and assignment.
            call: The tool call to run; validated against its own schema here regardless of
                whether the caller (a degradation ladder, or a test) already validated it.

        Returns:
            The tool's result text on success; a readable error string for an unknown tool, a
            schema violation, or a `ToolError` the runner raised.

        Raises:
            hivemind.workers.roles.drone.HandoffRequestedError: The runner asked to hand off;
                propagates unchanged, never caught here.
            hivemind.workers.errors.WorkerCancelledError: Cancellation was noticed while running;
                propagates unchanged, never caught here.
        """
        spec = self._specs.get(call.name)
        if spec is None:
            # Untrusted model output naming a tool that was never offered: a readable string, not
            # a raise (codingrules section 15).
            return f"no tool named {call.name!r} is offered."
        errors = validate_arguments(spec.definition.parameters, call.arguments)
        if errors:
            return "; ".join(errors)
        try:
            return await spec.run(invocation, call.arguments)
        except ToolError as exc:
            # A runner's own deliberate stop becomes its message; every other exception (a control
            # exception such as HandoffRequestedError or WorkerCancelledError included) propagates.
            return str(exc)


def build_registry(ctx: WorkerContext) -> ToolRegistry:
    """Build the ToolRegistry one Drone attempt offers, from what `ctx.capabilities` allows.

    Args:
        ctx: This attempt's WorkerContext; only `capabilities` decides which tools are offered.

    Returns:
        A ToolRegistry with `run_command`, `read_file`, `write_file` and `ask` always, plus
        `http_request` only when `ctx.capabilities` holds at least one `net` capability -- there is
        nothing else a network tool could ever be allowed to do for this Worker.
    """
    # Imported here, not at module level: session/http/ask each import ToolInvocation/ToolSpec
    # from this module, so importing them back at module scope would cycle.
    from hivemind.workers.tools.ask import ASK_SPEC
    from hivemind.workers.tools.http import HTTP_SPEC
    from hivemind.workers.tools.session import READ_FILE_SPEC, RUN_COMMAND_SPEC, WRITE_FILE_SPEC

    specs: list[ToolSpec] = [RUN_COMMAND_SPEC, READ_FILE_SPEC, WRITE_FILE_SPEC, ASK_SPEC]
    if any(capability.family is CapabilityFamily.NET for capability in ctx.capabilities):
        specs.append(HTTP_SPEC)
    return ToolRegistry(specs)
