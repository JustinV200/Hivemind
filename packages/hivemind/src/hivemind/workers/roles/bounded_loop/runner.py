"""Define run_bounded_loop: the one attempt shape every bounded-loop role's `run` delegates to.

`run_bounded_loop` is `hivemind.workers.roles.drone.role.Drone.run`'s own former body, factored
out and parametrised by a `hivemind.workers.roles.bounded_loop.profile.RoleProfile` (roadmap step
6.9), so the Forager and Scout reuse it verbatim instead of copying it: assemble a prompt from
durable state through `hivemind.memory.assemble`, hand the model the tools the profile's own
`build_tools` returns, and let `hivemind.llm.run_tool_loop` drive the turns; every tool call that
has a side effect goes through the Capping gate before it lands (codingrules 8.12). `_Attempt`
bundles one attempt's own fixed collaborators (codingrules 5.1: "introduce a frozen dataclass for
the argument group"), built once by `_build_attempt` and read by `_run_once`, so this module's own
public function stays short. One awake episode per attempt (codingrules 8.8, 8.9): a
`hivemind.llm.errors.ContextTooLongError` shrinks the starting budget and retries through
`hivemind.memory.run_with_overflow_retry`, rather than crashing the attempt; past `MAX_OVERFLOWS`
it raises `hivemind.memory.overflow.ContextOverflowError`, which this function lets propagate
uncaught, exactly like any other role bug. Two ways an attempt ends early instead of finishing its
loop: `HandoffRequestedError` (this attempt's own telemetry crossed the handoff threshold) becomes
a `claimed=False` outcome carrying a Handoff; `LoopStoppedError` (a tool, the Scout's
`report_findings`, ended the loop itself with an already-built outcome) is returned with this
attempt's own telemetry spend overlaid on it.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.bounded_loop`. Called once
    per attempt by every bounded-loop role's own `run` (`hivemind.workers.roles.drone.role.Drone`,
    `.forager.role.Forager`, `.scout.role.Scout`). Calls into `hivemind.llm` (the tool loop and the
    trail observer), `hivemind.memory`, this package's `.executor`, `.outcome`, `.prompt` and
    `.sources`, and waggle only.

Key invariants:
    - Never marks a task SUCCEEDED (codingrules 8.7): a `claimed=True` outcome says only that the
      role believes the work is done.
    - Holds no conversation between attempts; a handoff carries what the next attempt needs.
    - A `ContextTooLongError` never crashes this attempt: it is caught and retried, with a shrunk
      budget, inside `run_with_overflow_retry`; only `ContextOverflowError` (after `MAX_OVERFLOWS`
      retries), `WorkerCancelledError` or a role bug ever escapes this function.

See Also:
    - .claude/codingrules.md sections 5.1, 5.4, 8.8, 8.9 and 8.12.
    - hivemind.workers.roles.bounded_loop.profile for RoleProfile, this function's one extra
      parameter over `hivemind.workers.base.Worker.run`'s own three.
    - hivemind.workers.roles.drone.role for Drone, the first role built on this function.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.llm import ToolLoopOptions, ToolLoopResult, TrailLadderObserver, run_tool_loop
from hivemind.memory import Handoff, TokenBudget, run_with_overflow_retry
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.bounded_loop.executor import (
    HandoffRequestedError,
    LoopExecutor,
    LoopStoppedError,
    collect_artifacts,
)
from hivemind.workers.roles.bounded_loop.outcome import build_claimed_outcome, build_handoff_outcome
from hivemind.workers.roles.bounded_loop.profile import RoleProfile
from hivemind.workers.roles.bounded_loop.prompt import (
    assemble_role_prompt,
    build_request,
    initial_budget,
)
from hivemind.workers.roles.bounded_loop.sources import RoleSources
from hivemind.workers.tools import ToolInvocation, ToolRegistry
from waggle.messages.task import TaskAssign

__all__ = ["run_bounded_loop"]


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One attempt's fixed collaborators, built once by `_build_attempt` (module docstring)."""

    ctx: WorkerContext
    assignment: TaskAssign
    profile: RoleProfile
    registry: ToolRegistry
    sources: RoleSources
    executor: LoopExecutor
    options: ToolLoopOptions


async def run_bounded_loop(
    ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None, profile: RoleProfile
) -> WorkerOutcome:
    """Run one attempt at `assignment` under `profile`: assemble a prompt, then loop tool calls.

    Args:
        ctx: Everything this attempt may use.
        assignment: What was assigned: the objective, acceptance criteria, tempo, clearance,
            grant and recon.
        resume_from: The Handoff to resume from, when this attempt continues an earlier one.
        profile: This role's own knobs (module docstring): prompt, round cap, tool selection and
            an optional per-tool-call hook.

    Returns:
        A `claimed=True` WorkerOutcome once the loop stops wanting to call tools, a `claimed=False`
        one carrying a Handoff once this attempt's own telemetry says to checkpoint, or whatever
        outcome a tool itself ended the loop with (`LoopStoppedError`).
    """
    attempt = _build_attempt(ctx, assignment, resume_from, profile)

    async def _episode(budget: TokenBudget) -> ToolLoopResult:
        """Assemble this attempt's prompt at `budget` and run the tool loop once."""
        return await _run_once(attempt, budget)

    try:
        # A ContextTooLongError here is caught and retried, with a shrunk budget, inside
        # run_with_overflow_retry; only ContextOverflowError (after MAX_OVERFLOWS retries),
        # HandoffRequestedError or LoopStoppedError ever escape this try (module docstring).
        starting = initial_budget(ctx, profile.budget_fraction, profile.output_reserve_tokens)
        result = await run_with_overflow_retry(
            _episode, starting, ctx.trail, ctx.identity, ctx.clock
        )
    except HandoffRequestedError:
        # This attempt's own telemetry asked to checkpoint; hand back a Handoff instead of a
        # claim, so hivemind.workers.runtime.WorkerRuntime can reset and resume it.
        return await build_handoff_outcome(ctx, assignment, attempt.executor)
    except LoopStoppedError as stopped:
        # A tool (the Scout's report_findings) already built the outcome; overlay this attempt's
        # own telemetry spend the same way build_handoff_outcome does, so a raiser never has to
        # read ctx.telemetry itself (LoopStoppedError's own docstring).
        return stopped.outcome.model_copy(update={"spend_usd": ctx.telemetry.snapshot().spend})
    _record_usage(ctx, result)
    artifacts = await collect_artifacts(ctx, attempt.executor.records)
    return build_claimed_outcome(assignment, result, artifacts)


def _build_attempt(
    ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None, profile: RoleProfile
) -> _Attempt:
    """Build this attempt's fixed collaborators from `profile`, once, before any round runs."""
    registry = profile.build_tools(ctx)
    sources = RoleSources(ctx, assignment, resume_from)
    invocation = ToolInvocation(ctx=ctx, assignment=assignment)
    executor = LoopExecutor(
        registry, invocation, ctx.telemetry, ctx.handoff_threshold, profile.on_tool_result
    )
    observer = TrailLadderObserver(
        ctx.trail, ctx.identity.hive_id, ctx.identity.node_id, ctx.identity.actor, ctx.clock
    )
    options = ToolLoopOptions(max_rounds=profile.max_rounds, gate=ctx.call_gate, observer=observer)
    return _Attempt(ctx, assignment, profile, registry, sources, executor, options)


async def _run_once(attempt: _Attempt, budget: TokenBudget) -> ToolLoopResult:
    """Assemble one prompt at `budget` from `attempt`'s own sources and run the tool loop once."""
    prompt = await assemble_role_prompt(
        attempt.ctx, attempt.assignment, attempt.sources, budget, attempt.profile.principal_role
    )
    tools = attempt.registry.definitions()
    request = build_request(attempt.ctx, prompt, attempt.assignment, tools, attempt.profile)
    return await run_tool_loop(attempt.ctx.bound, request, tools, attempt.executor, attempt.options)


def _record_usage(ctx: WorkerContext, result: ToolLoopResult) -> None:
    """Record this attempt's aggregated tokens and spend on telemetry, once, after the loop ends.

    `hivemind.llm.run_tool_loop` exposes no per-round hook a caller could record from between
    turns, so this records the loop's one summed `Usage` instead of one entry per round.
    """
    used = result.usage.input_tokens + result.usage.output_tokens
    ctx.telemetry.record_tokens(used, ctx.bound.context_window)
    if result.usage.cost_usd is not None:
        ctx.telemetry.add_spend(result.usage.cost_usd)
