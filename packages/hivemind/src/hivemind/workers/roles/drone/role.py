"""Define Drone: the generic Worker role that runs one bounded tool loop per attempt.

`Drone.run` delegates to `hivemind.workers.roles.bounded_loop.runner.run_bounded_loop` (roadmap
step 6.9) with a `RoleProfile` built from this role's own knobs: `WorkerRole.DRONE`, `DRONE_ROLE`
for hot-state packing, `PromptName.DRONE_SYSTEM`, `DRONE_MAX_ROUNDS`, and `hivemind.workers.tools.
build_registry` for its tool selection -- every tool its capabilities allow, with no restriction
and no per-call hook, exactly as before this factoring. The class lives here rather than in the
package face because a face only re-exports (codingrules 5.4); `hivemind.workers.roles.drone.
prompt` supplies this role's own constants to the shared prompt builders.

Fits into the Hive:
    Layer 4 (roles that do the work). Instantiated by a Warden's `worker_factory`
    (`hivemind.workers.roles.worker_for`) and driven by `hivemind.workers.runtime.WorkerRuntime`.
    Calls into `hivemind.workers.roles.bounded_loop`, `hivemind.workers.roles.drone.prompt`,
    `hivemind.workers.tools` and waggle only.

Key invariants:
    - A Drone never marks its own work SUCCEEDED: it returns `WorkerOutcome(claimed=True)` and
      the Warden's acceptance decides (codingrules 8.12).
    - Its round cap, prompt and tool selection are exactly what they were before roadmap step
      6.9's factoring: `DRONE_MAX_ROUNDS`, `PromptName.DRONE_SYSTEM`, `build_registry`.

See Also:
    - .claude/codingrules.md sections 5.4, 8.8, 8.9 and 8.12.
    - hivemind.workers.roles.bounded_loop.runner for run_bounded_loop, this class's one delegate.
    - hivemind.workers.roles.drone for the package face this module is re-exported through.
    - hivemind.workers.base for the Worker protocol this class satisfies.
"""

from __future__ import annotations

from hivemind.llm import PromptName
from hivemind.memory import Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.bounded_loop import RoleProfile, run_bounded_loop
from hivemind.workers.roles.drone.prompt import (
    DRONE_BUDGET_FRACTION,
    DRONE_OUTPUT_RESERVE_TOKENS,
    DRONE_ROLE,
)
from hivemind.workers.tools import build_registry
from waggle.messages.task import TaskAssign, WorkerRole

DRONE_MAX_ROUNDS = 12  # Generous for a real task, small enough to bound a runaway loop.

__all__ = ["DRONE_MAX_ROUNDS", "Drone"]


class Drone:
    """The generic, disposable Worker role: work one task through a bounded, tool-calling loop.

    `role` is a fixed property (codingrules section 8.1: implements `hivemind.workers.base.Worker`
    structurally); a fresh `Drone` instance is built per attempt by whichever composition root
    spawns it (`hivemind.workers.roles.worker_for`).
    """

    def __init__(self, *, max_rounds: int = DRONE_MAX_ROUNDS) -> None:
        """Build a Drone with a fixed round cap for its tool loop.

        Args:
            max_rounds: The most model turns one attempt's tool loop may take before it reports
                exhaustion instead of a final answer; `DRONE_MAX_ROUNDS` by default.
        """
        self._profile = RoleProfile(
            role=WorkerRole.DRONE,
            principal_role=DRONE_ROLE,
            prompt_name=PromptName.DRONE_SYSTEM,
            max_rounds=max_rounds,
            build_tools=build_registry,
            budget_fraction=DRONE_BUDGET_FRACTION,
            output_reserve_tokens=DRONE_OUTPUT_RESERVE_TOKENS,
        )

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
        return await run_bounded_loop(ctx, assignment, resume_from, self._profile)
