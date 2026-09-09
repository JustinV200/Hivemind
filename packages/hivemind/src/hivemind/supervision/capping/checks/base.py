"""Define Check and CheckContext: the seam every rung of a risk tier's ladder implements.

Codingrules section 8.12: "checks are layered, cheapest first." `Check` is the `typing.Protocol`
(codingrules section 8.1) every rung of that ladder implements -- the deterministic ones in
`hivemind.supervision.capping.checks.deterministic` in v0, and a sandbox-test, judge or human rung
in a later phase, all behind the same seam. `CheckContext` is everything one check needs to decide
pass or fail: the proposal, the capability set the Warden actually holds (`hivemind.guard.
CapabilitySet` -- Capping needs this to enforce codingrules section 15's "least privilege is code,
not policy" on a proposal's own paths and commands), the lease view, the scratch root and the tier
spec it is being checked against. `CheckResultRecord` is one rung's outcome.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package.
    Implemented by every class in `hivemind.supervision.capping.checks.deterministic`; built by
    `hivemind.supervision.capping.gate.CappingGate` for every check it runs. Calls into
    `hivemind.guard` (CapabilitySet), `hivemind.supervision.capping.lease_view`,
    `hivemind.supervision.capping.proposal`, `hivemind.supervision.capping.tiers` and waggle only.

Key invariants:
    - CheckContext is a frozen dataclass (codingrules section 8.5): nothing a Check runs ever
      mutates the context it was handed.
    - CheckResultRecord is frozen and forbids extras, like every boundary value in this repository.
    - A conforming Check never awaits a model or the network (codingrules section 8.12: "the
      deterministic checks" run in autopilot, no model); a later phase's SANDBOX_TESTS, JUDGE and
      HUMAN rungs are the ones that do.

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this module follows.
    - .claude/codingrules.md section 8.12 for the check-ladder shape this module implements.
    - .claude/codingrules.md section 15 for "Least privilege is code, not policy," the rule
      PathAllowlistCheck and CommandAllowlistCheck enforce using `CheckContext.capabilities`.
    - hivemind.guard for CapabilitySet, the value CheckContext.capabilities carries.
    - hivemind.supervision.capping.checks.deterministic for this phase's four concrete checks.
    - hivemind.supervision.capping.gate for CappingGate, which builds a CheckContext per proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.guard import CapabilitySet
from hivemind.supervision.capping.lease_view import LeaseView
from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.tiers import TierSpec
from waggle.messages.capping import CheckKind, CheckOutcome

__all__ = ["Check", "CheckContext", "CheckResultRecord"]


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Everything one Check needs to decide pass or fail for one proposal."""

    proposal: Proposal  # The proposal being checked.
    capabilities: CapabilitySet  # What the proposing bee's Warden actually holds.
    lease: LeaseView  # The lease's scratch root, allowed paths and touched-path bookkeeping.
    scratch_root: Path  # The session's scratch directory; every relative path resolves here.
    tier: TierSpec  # The proposal's risk tier's configured checks, floor, snapshot and diff cap.


class CheckResultRecord(BaseModel):
    """One rung's outcome: which check ran, pass or fail, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CheckKind = Field(description="Which rung ran.")
    outcome: CheckOutcome = Field(description="The result.")
    reason: str = Field(description="Why, in one line; never recorded on the trail verbatim.")


class Check(Protocol):
    """One rung of a risk tier's check ladder.

    Implementations must be safe to call concurrently: a Warden may run more than one proposal's
    checks at once. `kind` is a ClassVar (codingrules 8.5: a Check instance carries no state of
    its own beyond, at most, the sub-checks `_AllowlistCheck` composes), matching every concrete
    check in `hivemind.supervision.capping.checks.deterministic`.
    """

    kind: ClassVar[CheckKind]

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Run this check against `context.proposal` and report the outcome.

        Args:
            context: The proposal plus everything needed to judge it.

        Returns:
            This check's outcome; never raises for an ordinary failure (that is what `outcome`
            reports), only for a genuinely unexpected condition.
        """
        ...
