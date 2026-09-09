"""Define Worker and WorkerOutcome: the one Protocol every role implements, and what it returns.

A Worker is a subagent a Warden spawns to run one role (Forager, Scout, GuardBee, Undertaker,
Drone, HouseBee -- `waggle.messages.task.WorkerRole` names the six). This module fixes the
Protocol every role implements (codingrules section 8.1: "Anything with more than one plausible
implementation... is defined as a `typing.Protocol`") and the one shape its `run` coroutine
returns: `WorkerOutcome`, either a claim that the work is done (`claimed=True`, no `handoff`) or a
request to hand off and be resumed (`claimed=False`, `handoff` set) -- never both, and never
neither. A Worker never marks itself `SUCCEEDED`: `claimed` only says the role believes the work
is done, and its Warden (roadmap step 3.19) runs acceptance checks before a task ever reaches that
terminal state (codingrules section 8.7's counterpart for tasks; roadmap step 3.18).

Fits into the Hive:
    Layer 4 (roles that do the work). Implemented by every role under `hivemind.workers.roles`
    (roadmap step 3.16 adds the Drone, the first one); called once per attempt by
    `hivemind.workers.runtime.WorkerRuntime` (roadmap step 3.15), which interprets the returned
    WorkerOutcome and, on an uncaught exception instead, raises an Alarm rather than crashing.
    Calls into `hivemind.cell` (HoneyClearance), `hivemind.memory` (Handoff),
    `hivemind.workers.context` (WorkerContext) and waggle only.

Key invariants:
    - WorkerOutcome is frozen and forbids extras like every boundary value in this repository.
    - Exactly one of `claimed` and `handoff is not None` holds on every WorkerOutcome (a
      validator): `claimed=True` means the work is done and acceptance has not yet run;
      `handoff` set means the role stopped early to checkpoint and be resumed, not done yet.
    - `spend_usd` is never negative.

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this module follows.
    - .claude/codingrules.md section 8.7 for the "a Worker never marks itself SUCCEEDED" pattern
      this mirrors for tasks.
    - hivemind.workers.context for WorkerContext, `run`'s one collaborator bundle.
    - hivemind.workers.runtime for WorkerRuntime, the one caller of `run` and the one interpreter
      of WorkerOutcome.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.cell import HoneyClearance
from hivemind.memory import Handoff
from hivemind.workers.context import WorkerContext
from waggle.messages.task import ArtifactRef, TaskAssign, WorkerRole

MIN_SPEND_USD = 0.0  # Spend is never negative; the Hive meters cost, never refunds.

__all__ = ["Worker", "WorkerOutcome"]


class WorkerOutcome(BaseModel):
    """What one attempt at a role's `run` produces: a claim of completion, or a request to hand off.

    Exactly one of `claimed` and `handoff` is set (a validator): `claimed=True` with `handoff=None`
    means the role believes the work is done, and its Warden still has to run acceptance before
    the task reaches SUCCEEDED; `claimed=False` with `handoff` set means the role stopped short
    (its own context threshold, or an `Intervene`) and wants
    `hivemind.workers.runtime.WorkerRuntime` to checkpoint it and either resume it or, if told to
    stop, close the attempt as cancelled.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(description="One paragraph of what was done or where the role stopped.")
    clearance: HoneyClearance = Field(description="The label of `summary` and of `artifacts`.")
    artifacts: tuple[ArtifactRef, ...] = Field(
        default=(), description="Outputs produced so far, by path, size and digest."
    )
    claimed: bool = Field(
        description="True when the role believes the work is done; a Worker never marks itself "
        "SUCCEEDED, only CLAIMED -- its Warden runs acceptance before that becomes final."
    )
    handoff: Handoff | None = Field(
        default=None,
        description="Set when the role stopped to hand off instead of finishing; None when "
        "`claimed` is True.",
    )
    spend_usd: float = Field(ge=MIN_SPEND_USD, description="What this attempt has spent so far.")

    @model_validator(mode="after")
    def _claimed_xor_handoff(self) -> WorkerOutcome:
        """Require exactly one of `claimed` and `handoff is not None`."""
        # A claim with a handoff would tell the runtime to both finish and checkpoint at once;
        # neither set would leave the runtime with nothing to do at all. Both are refused.
        if self.claimed == (self.handoff is not None):
            raise ValueError(
                f"WorkerOutcome requires exactly one of claimed and handoff, got "
                f"claimed={self.claimed} with handoff={'set' if self.handoff else None}."
            )
        return self


class Worker(Protocol):
    """One role's implementation: what model slot it runs on, and how it does one attempt.

    Implementations are registered by role in the composition root (`hivemind.wardens.spawn`,
    roadmap step 3.19) and constructed fresh per attempt; `hivemind.workers.runtime.WorkerRuntime`
    owns the one running instance's lifecycle.
    """

    @property
    def role(self) -> WorkerRole:
        """Which of the six Worker roles this implementation is."""
        ...

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Run one attempt at `assignment`, resuming from `resume_from` when given.

        Args:
            ctx: Everything this attempt may use: the Cell, the model, memory, the trail, a way
                to ask a blocking question, and this attempt's own telemetry.
            assignment: What the Warden assigned: the objective, acceptance criteria, Tempo,
                clearance and grant.
            resume_from: The Handoff to resume from, when this attempt continues an earlier one
                (a threshold reset, a rebind, a Warden migration); None for a fresh start.

        Returns:
            A WorkerOutcome: either a claim of completion, or a request to hand off and be
            resumed.

        Raises:
            Exception: Any uncaught exception ends this attempt; `WorkerRuntime` (the one caller)
                catches it, raises an Alarm and reports TaskResult(FAILED) rather than letting the
                exception propagate further -- see codingrules section 10's "top of a Worker's run
                loop" allowance.
        """
        ...
