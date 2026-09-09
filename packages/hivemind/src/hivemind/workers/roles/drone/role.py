"""Define Drone: the generic Worker role that runs one bounded tool loop per attempt.

`Drone.run` is one awake episode over hot state (codingrules 8.8, 8.9): it assembles its prompt
from durable state through `hivemind.memory.assemble`, hands the model the tools the Worker's
capabilities allow, and lets `hivemind.llm.run_tool_loop` drive the turns; every tool call
that has a side effect goes through the Capping gate before it lands (codingrules 8.12). The
class lives here rather than in the package face because a face only re-exports (codingrules
5.4); the sibling modules hold the prompt assembly, the hot-state sources and the outcome
builders this module composes.

Fits into the Hive:
    Layer 4 (roles that do the work). Instantiated by a Warden's `worker_factory` (roadmap
    3.19) and driven by `hivemind.workers.runtime.WorkerRuntime`. Calls into
    `hivemind.workers.roles.drone.{prompt,sources,outcome}`, `hivemind.workers.tools`,
    `hivemind.llm` (the tool loop and the trail observer) and `hivemind.memory`.

Key invariants:
    - A Drone never marks its own work SUCCEEDED: it returns `WorkerOutcome(claimed=True)` and
      the Warden's acceptance decides (codingrules 8.12).
    - It holds no conversation between attempts; a handoff carries what the next attempt needs.

See Also:
    - .claude/codingrules.md sections 5.4, 8.8, 8.9 and 8.12.
    - hivemind.workers.roles.drone for the package face this module is re-exported through.
    - hivemind.workers.base for the Worker protocol this class satisfies.
"""

from __future__ import annotations

from hivemind.llm import ToolLoopOptions, ToolLoopResult, TrailLadderObserver, run_tool_loop
from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.drone.outcome import (
    HandoffRequestedError,
    _RecordingExecutor,
    build_claimed_outcome,
    build_handoff_outcome,
    collect_artifacts,
)
from hivemind.workers.roles.drone.prompt import assemble_drone_prompt, build_request
from hivemind.workers.roles.drone.sources import DroneSources
from hivemind.workers.tools import ToolInvocation, build_registry
from waggle.messages.task import TaskAssign, WorkerRole

DRONE_MAX_ROUNDS = 12  # Generous for a real task, small enough to bound a runaway loop.

__all__ = ["DRONE_MAX_ROUNDS", "Drone"]


class Drone:
    """The generic, disposable Worker role: work one task through a bounded, tool-calling loop.

    `role` is a fixed property (codingrules section 8.1: implements `hivemind.workers.base.Worker`
    structurally); a fresh `Drone` instance is built per attempt by whichever composition root
    spawns it (a Warden, roadmap step 3.19).
    """

    def __init__(self, *, max_rounds: int = DRONE_MAX_ROUNDS) -> None:
        """Build a Drone with a fixed round cap for its tool loop.

        Args:
            max_rounds: The most model turns one attempt's tool loop may take before it reports
                exhaustion instead of a final answer; `DRONE_MAX_ROUNDS` by default.
        """
        self._max_rounds = max_rounds

    @property
    def role(self) -> WorkerRole:
        """This role is always DRONE."""
        return WorkerRole.DRONE

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Run one attempt at `assignment`: assemble a prompt, then loop tool calls to a stop.

        Args:
            ctx: Everything this attempt may use.
            assignment: What was assigned: the objective, acceptance criteria, tempo, clearance
                and grant.
            resume_from: The Handoff to resume from, when this attempt continues an earlier one.

        Returns:
            A `claimed=True` WorkerOutcome once the loop stops wanting to call tools, or a
            `claimed=False` one carrying a Handoff once this attempt's own telemetry says to
            checkpoint instead.
        """
        registry = build_registry(ctx)
        sources = DroneSources(ctx, assignment, resume_from)
        prompt = await assemble_drone_prompt(ctx, assignment, sources)
        request = build_request(ctx, prompt, assignment, registry.definitions())
        invocation = ToolInvocation(ctx=ctx, assignment=assignment)
        executor = _RecordingExecutor(registry, invocation, ctx.telemetry, ctx.handoff_threshold)
        observer = TrailLadderObserver(
            ctx.trail, ctx.identity.hive_id, ctx.identity.node_id, ctx.identity.actor, ctx.clock
        )
        options = ToolLoopOptions(
            max_rounds=self._max_rounds, gate=ctx.call_gate, observer=observer
        )
        try:
            result = await run_tool_loop(
                ctx.bound, request, registry.definitions(), executor, options
            )
        except HandoffRequestedError:
            # This attempt's own telemetry asked to checkpoint; hand back a Handoff instead of a
            # claim, so hivemind.workers.runtime.WorkerRuntime can reset and resume it.
            return build_handoff_outcome(ctx, assignment, executor.records)
        _record_usage(ctx, result)
        artifacts = await collect_artifacts(ctx, executor.records)
        return build_claimed_outcome(assignment, result, artifacts)


def _record_usage(ctx: WorkerContext, result: ToolLoopResult) -> None:
    """Record this attempt's aggregated tokens and spend on telemetry, once, after the loop ends.

    `hivemind.llm.run_tool_loop` exposes no per-round hook a caller could record from between
    turns (this package's own module docstring), so this records the loop's one summed `Usage`
    instead of one entry per round.
    """
    used = result.usage.input_tokens + result.usage.output_tokens
    ctx.telemetry.record_tokens(used, ctx.bound.context_window)
    if result.usage.cost_usd is not None:
        ctx.telemetry.add_spend(result.usage.cost_usd)
