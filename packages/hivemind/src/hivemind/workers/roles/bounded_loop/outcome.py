"""Define build_claimed_outcome and build_handoff_outcome: the two ordinary ways one attempt ends.

Matches `hivemind.workers.base.WorkerOutcome`'s own claimed-xor-handoff shape: a normal finish
becomes a `claimed=True` outcome with whatever the tool loop's final text said, and a checkpoint
becomes a `claimed=False` outcome carrying a fully populated `hivemind.memory.Handoff`, built from
`hivemind.workers.roles.bounded_loop.fields`. (The Scout's `report_findings` tool ends its loop a
third way, `hivemind.workers.roles.bounded_loop.executor.LoopStoppedError`, which carries its own
already-built outcome and never reaches this module.) Moved out of `hivemind.workers.roles.drone.
outcome.build` unchanged (roadmap step 6.9): neither function was ever Drone-specific.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.bounded_loop`. Used by
    `hivemind.workers.roles.bounded_loop.runner.run_bounded_loop`. Calls into `hivemind.cell`,
    `hivemind.llm`, `hivemind.memory`, `hivemind.workers.base`, `hivemind.workers.context`, this
    package's `.executor` and `.fields`, and waggle only.

Key invariants:
    - Every free-text field a built Handoff carries is truncated to the same caps
      `hivemind.memory.handoff.Handoff` itself validates against, so construction never raises.
    - A role never marks its own work SUCCEEDED (codingrules section 8.7): `build_claimed_outcome`
      only ever returns `claimed=True`, never a task status.

See Also:
    - hivemind.memory.handoff for Handoff and Decision, the shapes `build_handoff_outcome` builds.
    - hivemind.workers.roles.bounded_loop.fields for every Handoff field this module fills from
      the attempt's own recorded calls.
    - hivemind.workers.roles.bounded_loop.runner for run_bounded_loop, this module's one caller.
    - hivemind.workers.roles.drone.outcome.build for the re-export that keeps the Drone's own
      tests, which import these names from that path directly, unchanged.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.llm import ToolLoopResult
from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.bounded_loop import fields
from hivemind.workers.roles.bounded_loop.executor import LoopExecutor
from waggle.messages.task import ArtifactRef, TaskAssign

MAX_SUMMARY_CHARS = 4_000  # A paragraph or two; matches Handoff.progress's own scale.
# Mirror hivemind.memory.handoff's own per-field caps (a non-init submodule this package may not
# import from directly, codingrules section 5.4): truncating to these numbers keeps every
# Handoff this module builds within that model's own validated bounds.
_MAX_GOAL_CHARS = 2_000  # Mirrors MAX_GOAL_CHARS.
_MAX_PROGRESS_CHARS = 4_000  # Mirrors MAX_PROGRESS_CHARS.

__all__ = ["MAX_SUMMARY_CHARS", "build_claimed_outcome", "build_handoff_outcome"]


def build_claimed_outcome(
    assignment: TaskAssign, result: ToolLoopResult, artifacts: tuple[ArtifactRef, ...]
) -> WorkerOutcome:
    """Build the WorkerOutcome for an attempt that finished its tool loop normally.

    Args:
        assignment: The task this attempt worked; supplies the outcome's clearance.
        result: What `hivemind.llm.run_tool_loop` returned.
        artifacts: Every file `write_file` produced under scratch, from `collect_artifacts`.

    Returns:
        A `claimed=True` WorkerOutcome; a role never marks itself SUCCEEDED (codingrules section
        8.7), only that it believes the work is done. `scout_report` is left at its default
        `None`: a Scout that ends up here never filed one (its own `run` overrides this case).
    """
    summary = result.final_text.strip() or "The role finished its tool loop with no closing text."
    return WorkerOutcome(
        summary=summary[:MAX_SUMMARY_CHARS],
        clearance=HoneyClearance.from_wire(assignment.clearance),
        artifacts=artifacts,
        claimed=True,
        handoff=None,
        spend_usd=result.usage.cost_usd or 0.0,
    )


async def build_handoff_outcome(
    ctx: WorkerContext, assignment: TaskAssign, executor: LoopExecutor
) -> WorkerOutcome:
    """Build the WorkerOutcome for an attempt that stopped early to hand off.

    Every guidance field below is derived from `executor.records` (and, for `pinned_facts`/
    `next_steps`, from this attempt's own pins and the assignment's own acceptance criteria) by
    `hivemind.workers.roles.bounded_loop.fields`.

    Args:
        ctx: This attempt's WorkerContext; supplies `worker_id`, the pins a resuming bee should
            see verbatim, and the telemetry spend recorded so far.
        assignment: The task this attempt was working; supplies the Handoff's goal, clearance and
            acceptance criteria (`next_steps`).
        executor: This attempt's own `LoopExecutor`; `records` is every tool call it made before
            the handoff, `pending_call` the one it refused to run, if any (becomes the Handoff's
            one `open_threads` entry).

    Returns:
        A `claimed=False` WorkerOutcome carrying a fully populated Handoff.
    """
    records = executor.records
    clearance = HoneyClearance.from_wire(assignment.clearance)
    handoff = Handoff(
        goal=assignment.objective[:_MAX_GOAL_CHARS],
        progress=fields.summarise_progress(records)[:_MAX_PROGRESS_CHARS],
        decisions=fields.decisions_from_records(records),
        tried_and_failed=fields.tried_and_failed_lines(records),
        constraints=fields.constraint_lines(records),
        open_threads=fields.open_thread_lines(executor.pending_call),
        next_steps=await fields.next_step_lines(ctx, assignment),
        do_not_redo=fields.do_not_redo_lines(records),
        pinned_facts=await fields.pinned_fact_lines(ctx, clearance),
        notes="",
        clearance=clearance,
        written_by=str(ctx.worker_id),
        task_id=assignment.task_id,
    )
    return WorkerOutcome(
        summary="Checkpointing to hand off; see the attached Handoff for progress and next steps.",
        clearance=clearance,
        artifacts=(),
        claimed=False,
        handoff=handoff,
        spend_usd=ctx.telemetry.snapshot().spend,
    )
