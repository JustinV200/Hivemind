"""Define Forager: the bounded see/act Worker role that gathers over a Cell's Exoskeleton.

A Forager (roadmap step 6.9) is a Drone-shaped role scoped to Exoskeleton work: it drives the
display, pointer, keyboard and browser a task's Cell was given, and deposits every page it reads
as Nectar (`hivemind.workers.roles.forager.nectar.deposit_nectar`) rather than leaving that content
to be rediscovered later. It reuses `hivemind.workers.roles.bounded_loop.runner.run_bounded_loop`
verbatim, the same shared machinery the Drone runs on: its own `RoleProfile` differs only in its
`WorkerRole`, its system prompt (`PromptName.FORAGER_SYSTEM`), a slightly larger round cap (web
work tends to need more turns per page than a shell task does) and the Nectar-deposit hook.
`build_tools` stays `hivemind.workers.tools.build_registry` unchanged: a Forager gets every tool
its capabilities allow, exactly like a Drone, so it can still read or write a scratch file
alongside driving the Exoskeleton.

A task placed for a Forager should always carry an Exoskeleton (the Queen-side planner rule: "a
FORAGER needs `needs.exoskeleton`"), so `ctx.exoskeleton is None` here is a placement bug, not a
routine condition. `run` refuses it before ever building a request: `WorkerOutcome`'s own
claimed-xor-handoff shape has no room for "this attempt cannot proceed and should not be retried
in place" (a `claimed=False` outcome must carry a Handoff, and the codebase's own convention
reserves a Handoff for a genuinely resumable checkpoint -- a context threshold or an Intervene,
never a permanently missing peripheral), so this refusal raises instead of returning one, exactly
the way `hivemind.workers.tools.exoskeleton.act.attached` already refuses an unreachable peripheral.
`hivemind.workers.runtime.WorkerRuntime` turns the raise into an Alarm and a `TaskResult(FAILED)`,
which is what should happen: a human or the Queen needs to see that this task was placed wrong,
not have the Warden resume it into the same refusal forever.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.forager`. Instantiated by a
    Warden's `worker_factory` (`hivemind.workers.roles.worker_for`) and driven by `hivemind.
    workers.runtime.WorkerRuntime`. Calls into `hivemind.workers.roles.bounded_loop`, `hivemind.
    workers.roles.forager.nectar`, `hivemind.workers.tools` and waggle only.

Key invariants:
    - Never calls the model without an attached Exoskeleton: `run` checks `ctx.exoskeleton` before
      building anything (module docstring).
    - A Forager never marks its own work SUCCEEDED (codingrules 8.7), the same as every other
      bounded-loop role.

See Also:
    - .claude/codingrules.md section 8.7 for "a Worker never marks itself SUCCEEDED."
    - .claude/roadmap.md step 6.9 for the Forager's own bullet.
    - hivemind.workers.roles.bounded_loop.runner for run_bounded_loop, this class's one delegate.
    - hivemind.workers.roles.forager.nectar for deposit_nectar, this role's own on_tool_result hook.
    - hivemind.workers.tools.exoskeleton.act for attached, the precedent this refusal follows.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.llm import PromptName
from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.errors import WorkerError
from hivemind.workers.roles.bounded_loop import RoleProfile, run_bounded_loop
from hivemind.workers.roles.forager.nectar import deposit_nectar
from hivemind.workers.tools import build_registry
from waggle.messages.task import TaskAssign, WorkerRole

FORAGER_MAX_ROUNDS = 16  # A little more than DRONE_MAX_ROUNDS (12): web work tends to take more
# turns per page (navigate, read, act, verify) than a shell task's own round.
FORAGER_ROLE = "forager"  # Principal.role for every Forager episode, matching DRONE_ROLE's shape.

__all__ = ["FORAGER_MAX_ROUNDS", "Forager", "ForagerRequiresExoskeletonError"]


class ForagerRequiresExoskeletonError(WorkerError):
    """Raise when a Forager is assigned a task with no attached Exoskeleton.

    A placement bug (module docstring), not a condition worth retrying: `hivemind.workers.
    runtime.WorkerRuntime` turns this into an Alarm and a `TaskResult(FAILED)` rather than a
    Handoff the Warden would only resume into the same refusal.
    """

    code: ClassVar[str] = "hivemind.workers.roles.forager.requires_exoskeleton"

    def __init__(self) -> None:
        """Build the error; carries no extra state beyond the missing Exoskeleton itself."""
        super().__init__(
            "A Forager was assigned a task with no attached Exoskeleton; it never calls the "
            "model without one. This task should have carried needs.exoskeleton at placement."
        )


class Forager:
    """The Exoskeleton-scoped Worker role: gather over a Cell's display, pointer and browser.

    `role` is a fixed property (codingrules section 8.1: implements `hivemind.workers.base.Worker`
    structurally); a fresh `Forager` instance is built per attempt by whichever composition root
    spawns it (`hivemind.workers.roles.worker_for`).
    """

    def __init__(self, *, max_rounds: int = FORAGER_MAX_ROUNDS) -> None:
        """Build a Forager with a fixed round cap for its tool loop.

        Args:
            max_rounds: The most model turns one attempt's tool loop may take before it reports
                exhaustion instead of a final answer; `FORAGER_MAX_ROUNDS` by default.
        """
        self._profile = RoleProfile(
            role=WorkerRole.FORAGER,
            principal_role=FORAGER_ROLE,
            prompt_name=PromptName.FORAGER_SYSTEM,
            max_rounds=max_rounds,
            build_tools=build_registry,
            on_tool_result=deposit_nectar,
        )

    @property
    def role(self) -> WorkerRole:
        """This role is always FORAGER."""
        return WorkerRole.FORAGER

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Run one attempt at `assignment`: assemble a prompt, then loop tool calls to a stop.

        Args:
            ctx: Everything this attempt may use; `ctx.exoskeleton` must be set.
            assignment: What was assigned: the objective, acceptance criteria, tempo, clearance,
                grant and recon (the Scout reports this task's dependencies produced, if any).
            resume_from: The Handoff to resume from, when this attempt continues an earlier one.

        Returns:
            A `claimed=True` WorkerOutcome once the loop stops wanting to call tools, or a
            `claimed=False` one carrying a Handoff once this attempt's own telemetry says to
            checkpoint instead.

        Raises:
            ForagerRequiresExoskeletonError: `ctx.exoskeleton` is `None` (module docstring).
        """
        if ctx.exoskeleton is None:
            raise ForagerRequiresExoskeletonError()
        return await run_bounded_loop(ctx, assignment, resume_from, self._profile)
