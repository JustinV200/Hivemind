"""Define HouseBee: the Worker-protocol adapter around one sweep (roadmap step 4.3).

`HouseBee.run` is `hivemind.workers.base.Worker`'s own shape (`ctx`, `assignment`, `resume_from` in,
a `WorkerOutcome` out) wrapped around `hivemind.workers.roles.house_bee.sweep.run_sweep`, the same
way `hivemind.workers.roles.drone.Drone.run` wraps `hivemind.llm.run_tool_loop`: it builds this
attempt's `SweepDeps`/`SweepWindow` from `ctx`, awaits one sweep, and reports its counts as a
`claimed=True` outcome (codingrules section 8.7: a Worker never marks a task SUCCEEDED; a House Bee
sweep especially never does, since it does not work toward any task's own acceptance criteria at
all). `resume_from` is accepted for protocol conformance and ignored: a sweep is stateless
maintenance, not a resumable unit of work, so there is nothing in a Handoff a sweep could use.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.house_bee`. Constructed by
    whichever composition root spawns a House Bee (a Warden, roadmap step 3.19, or a future
    timer-driven supervisor -- both out of this dispatch's scope: `queen/` and `wardens/` belong to
    a parallel dispatch) and run once per assignment by `hivemind.workers.runtime.WorkerRuntime`.
    Calls into `hivemind.cell` (HoneyClearance), `hivemind.memory` (BeeBread, MemoryContext),
    `hivemind.workers.base`, `hivemind.workers.context`, `hivemind.workers.roles.house_bee.sweep`
    and waggle only.

Key invariants:
    - `HouseBee.run` never marks a task SUCCEEDED (codingrules section 8.7): it always returns
      `claimed=True, handoff=None` -- a sweep has no failure mode that would call for a Handoff
      instead, since an interrupted sweep simply leaves whatever it had not yet reached for the
      next one to find.
    - `HouseBee.run` mirrors `hivemind.workers.roles.drone.prompt`'s own "no manifest reference"
      reasoning: `hivemind.workers.context.WorkerContext` carries none, so `HOUSE_BEE_HOT_WINDOW_S`
      mirrors the manifest's `[memory] hot_window_s` default as a fixed constant rather than
      reading it.

See Also:
    - .claude/codingrules.md section 8.7 for "a Worker never marks itself SUCCEEDED".
    - .claude/roadmap.md step 4.3 for this role's spec verbatim.
    - hivemind.workers.roles.house_bee.sweep for run_sweep, SweepDeps, SweepWindow and SweepOutcome,
      this module's one collaborator.
    - hivemind.workers.roles.drone.role for Drone, the sibling role this module's shape mirrors.
"""

from __future__ import annotations

from datetime import timedelta

from hivemind.cell import HoneyClearance
from hivemind.memory import BeeBread, Handoff, MemoryContext
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.house_bee.sweep import SweepDeps, SweepOutcome, SweepWindow, run_sweep
from waggle.messages.task import TaskAssign, WorkerRole

# Mirrors hivemind.manifest.schema.supervision.DEFAULT_HOT_WINDOW_S: WorkerContext carries no
# manifest reference for a role to read this from directly (hivemind.workers.roles.drone.prompt's
# own module docstring gives the same reasoning for DRONE_BUDGET_FRACTION).
HOUSE_BEE_HOT_WINDOW_S = 4.0 * 3600.0
MAX_SUMMARY_CHARS = 4_000  # Matches hivemind.workers.roles.drone.outcome.MAX_SUMMARY_CHARS's scale.

__all__ = ["HOUSE_BEE_HOT_WINDOW_S", "HouseBee"]


class HouseBee:
    """The maintenance role: one sweep (demote, then compact) per attempt, never a task's own work.

    `role` is a fixed property (codingrules section 8.1: implements `hivemind.workers.base.Worker`
    structurally); a fresh `HouseBee` instance is built per attempt by whichever composition root
    spawns it, matching `hivemind.workers.roles.drone.Drone`'s own shape.
    """

    @property
    def role(self) -> WorkerRole:
        """This role is always HOUSE_BEE."""
        return WorkerRole.HOUSE_BEE

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Run one sweep and report its counts; never resumes, never marks a task SUCCEEDED.

        Args:
            ctx: Everything this attempt may use; only `memory`, `bound`, `call_gate`, `identity`
                and `clock` are read (module docstring: a sweep is stateless maintenance).
            assignment: What was assigned; only `clearance` is read, as this sweep's own allowance.
            resume_from: Accepted for protocol conformance and ignored (module docstring).

        Returns:
            A `claimed=True` WorkerOutcome summarising the sweep's counts.
        """
        del resume_from  # A sweep is stateless maintenance; there is nothing to resume from.
        clearance = HoneyClearance.from_wire(assignment.clearance)
        deps = SweepDeps(
            memory=MemoryContext(store=ctx.memory, identity=ctx.identity, clock=ctx.clock),
            bee_bread=BeeBread(ctx.memory),
            bound=ctx.bound,
            gate=ctx.call_gate,
        )
        window = SweepWindow(
            now=ctx.clock.now(),
            hot_window=timedelta(seconds=HOUSE_BEE_HOT_WINDOW_S),
            allowance=clearance,
        )
        outcome = await run_sweep(deps, window)
        return _build_outcome(clearance, outcome)


def _build_outcome(clearance: HoneyClearance, outcome: SweepOutcome) -> WorkerOutcome:
    """Build the WorkerOutcome one HouseBee attempt reports: a claim, summarising `outcome`."""
    entry_word = "entry" if outcome.compacted_entries == 1 else "entries"
    summary = (
        f"Swept hot state and Bee Bread: demoted {outcome.demoted} item(s) into Bee Bread, "
        f"compacted {outcome.compacted_entries} {entry_word} into {outcome.compacted_batches} "
        f"summary/summaries, ripened {outcome.ripened} into Honey."
    )
    return WorkerOutcome(
        summary=summary[:MAX_SUMMARY_CHARS],
        clearance=clearance,
        artifacts=(),
        claimed=True,
        handoff=None,
        spend_usd=outcome.spend_usd,
    )
