"""Build a scripted GuiSurface: the Capping gate's GUI seam, answering as a test tells it to.

The gate applies, verifies and rolls back a GUI proposal only through a `GuiSurface` (ADR-0032);
`ScriptedSurface` stands in for the real one (`hivemind.exoskeleton.surface.ExoskeletonSurface`)
so gate-level tests can script whether steps apply, postconditions hold, a restore finds anything
and what evidence a judge is handed, and read back the order the gate called it in.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the unit tests of the
    Capping gate's GUI path and of the Warden's auditing gate.

Key invariants:
    - Every call is appended to `calls` in the order the gate made it, as one short string.

See Also:
    - hivemind.supervision.capping.gui for the protocol this follows.
"""

from __future__ import annotations

from hivemind.supervision.capping import (
    GuiApplyResult,
    JudgeEvidence,
    PostconditionOutcome,
    Proposal,
)
from waggle.messages.capping import RollbackMethod
from waggle.messages.labels import Postcondition

__all__ = ["ScriptedSurface"]


class ScriptedSurface:
    """A GuiSurface that records every call and answers as scripted."""

    def __init__(
        self,
        *,
        applies: bool = True,
        holds: bool = True,
        restores: bool = True,
        evidence: JudgeEvidence | None = None,
    ) -> None:
        """Script the steps, the postconditions, the restore and the evidence a judge is handed."""
        self.applies, self.holds, self.restores = applies, holds, restores
        self.recorded = evidence
        self.calls: list[str] = []

    async def before(self, proposal: Proposal) -> None:
        """Record the call."""
        self.calls.append("before")

    async def apply(self, proposal: Proposal) -> GuiApplyResult:
        """Record the call; succeed or fail the first step as scripted."""
        self.calls.append("apply")
        if self.applies:
            return GuiApplyResult(succeeded=True, steps_applied=len(proposal.action.gui))
        return GuiApplyResult(succeeded=False, steps_applied=0, failure_reason="step 1 failed")

    async def check(
        self, proposal: Proposal, index: int, pc: Postcondition
    ) -> PostconditionOutcome:
        """Record the call; hold or not as scripted."""
        self.calls.append(f"check {pc.kind.value}")
        return PostconditionOutcome(index=index, kind=pc.kind, has_held=self.holds, observed="seen")

    async def restore(self, proposal: Proposal) -> bool:
        """Record the call; find something to restore or not, as scripted."""
        self.calls.append("restore")
        return self.restores

    async def evidence(self, proposal: Proposal) -> JudgeEvidence | None:
        """Record the call; hand back the scripted evidence."""
        self.calls.append("evidence")
        return self.recorded

    async def finish(self, proposal: Proposal, rollback: RollbackMethod | None) -> None:
        """Record the terminal state and rollback method."""
        method = rollback.value if rollback is not None else None
        self.calls.append(f"finish {proposal.state.value} {method}")
