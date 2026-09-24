"""Define Scout: the strictly budgeted, read-only recon Worker role.

A Scout (roadmap step 6.10) looks around cheaply, on a strict budget of `SCOUT_MAX_ROUNDS` (about
six) tool-calling turns, before the Queen commits Foragers to a goal. It reuses `hivemind.workers.
roles.bounded_loop.runner.run_bounded_loop`, the same shared machinery the Drone runs on, but with
a narrow tool selection (`hivemind.workers.roles.scout.tools.scout_build_registry`: reads, a
GET-only http tool, and its own `report_findings`) and `PromptName.SCOUT_SYSTEM`. Calling
`report_findings` ends the loop at once, with a claimed outcome carrying the validated
`ScoutReport` (`hivemind.workers.roles.bounded_loop.executor.LoopStoppedError`); `run` overrides
the other ending, running out of rounds (or the model simply stopping) without ever filing one:
that case builds a conservative `feasible=False` report explaining why, writes it to
`SCOUT_REPORT_FILE` through the same capped path `report_findings` uses -- so the Warden's
FILE_EXISTS acceptance still passes and the Queen still sees a report, which is what lets its own
dependents be held back rather than left dangling on a task that never reported anything at all --
and returns it the same way a filed report would be.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.scout`. Instantiated by a
    Warden's `worker_factory` (`hivemind.workers.roles.worker_for`) and driven by `hivemind.
    workers.runtime.WorkerRuntime`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.llm`,
    `hivemind.workers.base`, `hivemind.workers.context`, `hivemind.workers.roles.bounded_loop`,
    `hivemind.workers.roles.scout.tools`, `hivemind.workers.tools` and waggle only.

Key invariants:
    - Every attempt that returns `claimed=True` carries a non-`None` `scout_report`: either
      `report_findings` filed one, or `run` built the exhaustion fallback (module docstring).
    - A `claimed=False` (handoff) outcome is returned unchanged: a checkpoint mid-attempt is not
      "out of rounds," and the resumed attempt gets its own fresh round budget.

See Also:
    - .claude/roadmap.md step 6.10 for the Scout's own bullet.
    - waggle.messages.task.recon for ScoutReport and SCOUT_REPORT_FILE.
    - hivemind.workers.roles.bounded_loop.runner for run_bounded_loop, this class's one delegate.
    - hivemind.workers.roles.scout.tools for scout_build_registry and report_findings.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.llm import PromptName
from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.bounded_loop import RoleProfile, run_bounded_loop
from hivemind.workers.roles.scout.tools import scout_build_registry
from hivemind.workers.tools import ToolInvocation, write_file
from waggle.messages.task import SCOUT_REPORT_FILE, ScoutReport, TaskAssign, WorkerRole

SCOUT_MAX_ROUNDS = 6  # Roadmap step 6.10: "a strict budget of about 6 rounds."
SCOUT_ROLE = "scout"  # Principal.role for every Scout episode, matching DRONE_ROLE's shape.
_EXHAUSTED_SUMMARY = (
    "Ran out of rounds before filing a report; treat this task as not yet assessed rather than "
    "as cleared to proceed."
)

__all__ = ["SCOUT_MAX_ROUNDS", "Scout"]


class Scout:
    """The strictly budgeted, read-only Worker role: recon before Foragers are committed.

    `role` is a fixed property (codingrules section 8.1: implements `hivemind.workers.base.Worker`
    structurally); a fresh `Scout` instance is built per attempt by whichever composition root
    spawns it (`hivemind.workers.roles.worker_for`).
    """

    def __init__(self, *, max_rounds: int = SCOUT_MAX_ROUNDS) -> None:
        """Build a Scout with a fixed round cap for its tool loop.

        Args:
            max_rounds: The most model turns one attempt's tool loop may take before this role
                falls back to an exhaustion report; `SCOUT_MAX_ROUNDS` by default.
        """
        self._profile = RoleProfile(
            role=WorkerRole.SCOUT,
            principal_role=SCOUT_ROLE,
            prompt_name=PromptName.SCOUT_SYSTEM,
            max_rounds=max_rounds,
            build_tools=scout_build_registry,
        )

    @property
    def role(self) -> WorkerRole:
        """This role is always SCOUT."""
        return WorkerRole.SCOUT

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Run one attempt at `assignment`: recon within budget, then report or fall back.

        Args:
            ctx: Everything this attempt may use.
            assignment: What was assigned: the objective, acceptance criteria (FILE_EXISTS on
                `SCOUT_REPORT_FILE`), tempo, clearance and grant.
            resume_from: The Handoff to resume from, when this attempt continues an earlier one.

        Returns:
            A `claimed=True` WorkerOutcome carrying a `ScoutReport` -- filed by `report_findings`,
            or this method's own exhaustion fallback when the loop ended without one; a
            `claimed=False` one carrying a Handoff when this attempt's own telemetry says to
            checkpoint instead (module docstring).
        """
        outcome = await run_bounded_loop(ctx, assignment, resume_from, self._profile)
        if not outcome.claimed or outcome.scout_report is not None:
            return outcome
        return await _exhausted_outcome(ctx, assignment, outcome)


async def _exhausted_outcome(
    ctx: WorkerContext, assignment: TaskAssign, outcome: WorkerOutcome
) -> WorkerOutcome:
    """Build and write the fallback report for a loop that ended without one (class docstring)."""
    report = ScoutReport(
        feasible=False,
        summary=_EXHAUSTED_SUMMARY,
        findings=(),
        suggested_steps=(),
        risks=(),
        targets=(),
    )
    invocation = ToolInvocation(ctx=ctx, assignment=assignment)
    # Through the same capped write path report_findings uses, so acceptance's own FILE_EXISTS
    # check still finds a report on disk even though this attempt never called that tool.
    await write_file(invocation, {"path": SCOUT_REPORT_FILE, "content": report.model_dump_json()})
    return WorkerOutcome(
        summary=report.summary,
        clearance=HoneyClearance.from_wire(assignment.clearance),
        artifacts=(),
        claimed=True,
        handoff=None,
        spend_usd=outcome.spend_usd,
        scout_report=report,
    )
