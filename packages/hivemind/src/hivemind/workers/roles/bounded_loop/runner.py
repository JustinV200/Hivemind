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
a `claimed=False` outcome carrying a Handoff, after the tally's spend (never its tokens, see
`_record_spend`) is booked on telemetry; `LoopStoppedError` (a tool, the Scout's
`report_findings`, ended the loop itself with an already-built outcome) is returned with this
attempt's own telemetry spend overlaid on it, after `_UsageTally` (the gate every call of the
attempt goes through) records what the unfinished loop never got to sum. Roadmap step 9.7: a
`hivemind.workers.tools.ToolsChangedError` (the Warden changed this Worker's Comb tools, as when a
requested tool was promoted and handed over) restarts the tool loop from state with a rebuilt
registry and a note naming the new tools, at most `MAX_TOOL_REBUILDS` times.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.bounded_loop`. Called once
    per attempt by every bounded-loop role's own `run` (`hivemind.workers.roles.drone.role.Drone`,
    `.forager.role.Forager`, `.scout.role.Scout`). Calls into `hivemind.llm` (the tool loop and the
    trail observer), `hivemind.memory`, this package's `.executor`, `.outcome`, `.prompt` and
    `.sources`, and waggle only.

Key invariants:
    - Never marks a task SUCCEEDED (codingrules 8.7): a `claimed=True` outcome says only that the
      role believes the work is done.
    - Holds no conversation between attempts; a handoff carries what the next attempt needs. A
      tool rebuild restarts the loop from state, as a context overflow does, and keeps the same
      executor, so its records (and the artifacts read from them) survive the restart.
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

from dataclasses import dataclass, field

from hivemind.cell import HoneyClearance
from hivemind.llm import (
    BoundModel,
    CallGate,
    LLMRequest,
    LLMResponse,
    ToolLoopOptions,
    ToolLoopResult,
    TrailLadderObserver,
    Usage,
    run_tool_loop,
)
from hivemind.memory import (
    Handoff,
    MemoryContext,
    Note,
    TokenBudget,
    add_note,
    run_with_overflow_retry,
)
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
from hivemind.workers.tools import ToolInvocation, ToolsChangedError
from waggle.ids import new_event_id
from waggle.messages.task import TaskAssign

# Roadmap step 9.7: how many times one attempt may restart its tool loop because the Warden changed
# its Comb tools. Each restart is a promotion or a re-attenuation; a handful covers a task that
# asks for a couple of tools, and the cap stops a Warden that keeps changing them from looping.
MAX_TOOL_REBUILDS = 4

__all__ = ["MAX_TOOL_REBUILDS", "run_bounded_loop"]


@dataclass(slots=True)
class _UsageTally:
    """Pass each of an attempt's calls to `gate`, keeping a running total of their Usage.

    `run_tool_loop` hands back its summed Usage only once the loop finishes; a tool that ends the
    loop itself (`LoopStoppedError`) leaves no ToolLoopResult behind, so without this tally the
    Scout's every report would record neither its tokens nor its spend.
    """

    gate: CallGate
    usage: Usage = field(default_factory=Usage.zero)

    async def complete(self, bound: BoundModel, request: LLMRequest) -> LLMResponse:
        """Make the call through `gate`, then add its Usage to the total; see `CallGate`."""
        response = await self.gate.complete(bound, request)
        self.usage = self.usage + response.usage
        return response


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One attempt's fixed collaborators, built once by `_build_attempt` (module docstring)."""

    ctx: WorkerContext
    assignment: TaskAssign
    profile: RoleProfile
    sources: RoleSources
    executor: LoopExecutor
    options: ToolLoopOptions
    tally: _UsageTally


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

    try:
        # A ContextTooLongError here is caught and retried, with a shrunk budget, inside
        # run_with_overflow_retry; a ToolsChangedError restarts the loop with a rebuilt registry
        # (_run_episodes); only ContextOverflowError (after MAX_OVERFLOWS retries),
        # HandoffRequestedError or LoopStoppedError ever escape this try (module docstring).
        result = await _run_episodes(attempt)
    except HandoffRequestedError:
        # This attempt's own telemetry asked to checkpoint; hand back a Handoff instead of a
        # claim, so hivemind.workers.runtime.WorkerRuntime can reset and resume it. Its calls
        # still cost money: book the tally's spend first, so the Handoff's outcome carries it.
        _record_spend(ctx, attempt.tally.usage)
        return await build_handoff_outcome(ctx, assignment, attempt.executor)
    except LoopStoppedError as stopped:
        # A tool (the Scout's report_findings) already built the outcome, and ended the loop
        # before it could sum its usage: record the gate's tally instead, then overlay this
        # attempt's own telemetry spend the same way build_handoff_outcome does, so a raiser
        # never has to read ctx.telemetry itself (LoopStoppedError's own docstring).
        _record_usage(ctx, attempt.tally.usage)
        return stopped.outcome.model_copy(update={"spend_usd": ctx.telemetry.snapshot().spend})
    # The tally, not `result.usage`: after a tool rebuild the final loop's usage is only its own.
    _record_usage(ctx, attempt.tally.usage)
    artifacts = await collect_artifacts(ctx, attempt.executor.records)
    claimed = build_claimed_outcome(assignment, result, artifacts)
    # The claim is the task's one TaskResult, so its spend is every attempt's, the handed-off
    # ones before this included: telemetry's running total, as the other two endings report.
    return claimed.model_copy(update={"spend_usd": ctx.telemetry.snapshot().spend})


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
    tally = _UsageTally(ctx.call_gate)  # Every call still goes through ctx.call_gate itself.
    options = ToolLoopOptions(max_rounds=profile.max_rounds, gate=tally, observer=observer)
    return _Attempt(ctx, assignment, profile, sources, executor, options, tally)


async def _run_episodes(attempt: _Attempt) -> ToolLoopResult:
    """Run the tool loop, restarting it from state when the Warden changes this Worker's tools.

    A promotion (or a Warden's re-attenuation) changes `ctx.comb_tools` under a running attempt.
    `LoopExecutor` notices and raises `ToolsChangedError`; the loop's conversation is gone, as it
    is after a context overflow, and the attempt's memory is assembled from state each time anyway
    (codingrules 8.9). So the registry is rebuilt, a note records which Comb tools are now offered
    (hot state shows it to the model), and the loop starts again with the same starting budget.
    """
    ctx = attempt.ctx

    async def _episode(budget: TokenBudget) -> ToolLoopResult:
        """Assemble this attempt's prompt at `budget` and run the tool loop once."""
        return await _run_once(attempt, budget)

    starting = initial_budget(
        ctx, attempt.profile.budget_fraction, attempt.profile.output_reserve_tokens
    )
    rebuilds = 0
    while True:
        try:
            return await run_with_overflow_retry(
                _episode, starting, ctx.trail, ctx.identity, ctx.clock
            )
        except ToolsChangedError:
            rebuilds += 1
            if rebuilds > MAX_TOOL_REBUILDS:
                raise
            # Built and swapped without an await between, so the executor's notion of the
            # bundle version is exactly the one this registry was built from.
            attempt.executor.use_registry(attempt.profile.build_tools(ctx))
            await _note_new_tools(attempt)


async def _note_new_tools(attempt: _Attempt) -> None:
    """Write the note that tells the restarted episode which Comb tools it may now call."""
    ctx = attempt.ctx
    bundle = ctx.comb_tools
    names = sorted(entry.name for entry in bundle.entries()) if bundle is not None else []
    text = (
        "Your tool set changed. The Comb tools you may call now: "
        f"{', '.join(names) if names else 'none'}. They are in your tools list; call one by name."
    )
    note = Note(
        id=new_event_id(ctx.clock),
        author=str(ctx.worker_id),
        text=text,
        clearance=HoneyClearance.from_wire(attempt.assignment.clearance),
        written_at=ctx.clock.now(),
    )
    # Local store write, milliseconds; the note is hot state the next prompt assembly reads.
    await add_note(note, MemoryContext(store=ctx.memory, identity=ctx.identity, clock=ctx.clock))


async def _run_once(attempt: _Attempt, budget: TokenBudget) -> ToolLoopResult:
    """Assemble one prompt at `budget` from `attempt`'s own sources and run the tool loop once."""
    prompt = await assemble_role_prompt(
        attempt.ctx, attempt.assignment, attempt.sources, budget, attempt.profile.principal_role
    )
    # Read from the executor on every run: a rebuilt registry replaces it between loops.
    tools = attempt.executor.registry.definitions()
    request = build_request(attempt.ctx, prompt, attempt.assignment, tools, attempt.profile)
    return await run_tool_loop(attempt.ctx.bound, request, tools, attempt.executor, attempt.options)


def _record_usage(ctx: WorkerContext, usage: Usage) -> None:
    """Record this attempt's aggregated tokens and spend on telemetry, once, after the loop ends.

    One summed `Usage`, not one entry per round: the loop's own total when it finished, or
    `_UsageTally`'s when a tool ended it early.
    """
    used = usage.input_tokens + usage.output_tokens
    ctx.telemetry.record_tokens(used, ctx.bound.context_window)
    _record_spend(ctx, usage)


def _record_spend(ctx: WorkerContext, usage: Usage) -> None:
    """Add `usage`'s cost, when its provider priced it, to this Worker's running spend.

    The handoff path records only this, never the token gauge: `record_tokens` overwrites the
    figure `should_hand_off` reads, and the same tracker carries over into the resumed attempt
    (`hivemind.workers.runtime.attempt.AttemptManager.start` resets only `handoff_requested`).
    The resumed attempt starts from a fresh prompt, so a gauge describing the context it just
    left would hand it off again before its first tool call, on every resume. Spend, by
    contrast, adds up across attempts: the grant paid for every one of them.
    """
    if usage.cost_usd is not None:
        ctx.telemetry.add_spend(usage.cost_usd)
