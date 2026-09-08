"""Define the capping family's gate entry: a proposal and each rung of its check ladder.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The
Capping gate is the quality gate every action with a side effect outside a lease's scratch
directory passes: the action is proposed with its risk tier and the postconditions the bee
expects, checked cheapest-first, applied, verified and rolled back on failure. The proposer
never verifies its own work; its Warden (the always-on supervisor of one Cell, a unit of
compute) runs the gate. This module is the gate's entry: ``ProposalSubmitted`` (the action, its
``RiskTier``, its postconditions, the task's Tempo and why) and ``CheckResult`` (one rung of the
ladder, a ``CheckKind`` with its ``CheckOutcome``). A proposal is identified by the MessageId of
its capping.proposal_submitted envelope. The action's own shape lives in
``waggle.messages.capping.action`` and the gate's word after the checks (``Verdict``,
``PostconditionResult``, ``RollbackDone``) in ``waggle.messages.capping.verdict``, split out by
responsibility so each file stays under the codingrules 5.1 size limit. Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by Workers (proposals) and checkers (a Warden's own check
    runner, a judge model or a human, reporting through the Warden) and read by Wardens and the
    Queen (the central orchestrator); calls into waggle.messages.base, waggle.messages.labels
    and waggle.messages.capping.action only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A proposal above READ_ONLY always states at least one postcondition, so nothing with a
      side effect is ever applied with nothing to verify afterwards.

See Also:
    - docs/waggle/spec.md section 8.9 for the normative fields, bounds and validators.
    - waggle.messages.capping.action for ProposedAction and ActionKind.
    - waggle.messages.capping.verdict for Verdict, PostconditionResult and RollbackDone.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    MessageIdField,
    TaskIdField,
    WaggleMessage,
    WorkerIdField,
)
from waggle.messages.capping.action import ProposedAction
from waggle.messages.labels import HoneyClearance, Postcondition, Tempo

MAX_POSTCONDITIONS = 32  # Assertions one action can state; an index into them fits in 0..31.
MAX_POSTCONDITIONS_CHARS = 65_536  # 64 KiB across every postcondition's text, so frames fit.
MIN_SPEND_ESTIMATE = 0.0  # Spend is never negative; 0 is the value for every tier but SPEND.
DEFAULT_SPEND_ESTIMATE = 0.0  # Most proposals spend nothing, so the field defaults to that.
MAX_DETAIL_CHARS = 8_000  # Lint output or failing test names: a screen or two, never a log.
MIN_DURATION_S = 0.0  # Wall-clock time never runs backwards.

__all__ = [
    "DEFAULT_SPEND_ESTIMATE",
    "MAX_DETAIL_CHARS",
    "MAX_POSTCONDITIONS",
    "MAX_POSTCONDITIONS_CHARS",
    "MIN_DURATION_S",
    "MIN_SPEND_ESTIMATE",
    "CheckKind",
    "CheckOutcome",
    "CheckResult",
    "ProposalSubmitted",
    "RiskTier",
]


class RiskTier(Enum):
    """A proposal's declared risk: which check ladder applies and whether apply is snapshotted."""

    READ_ONLY = "READ_ONLY"
    SCRATCH_WRITE = "SCRATCH_WRITE"
    OUTSIDE_SCRATCH_WRITE = "OUTSIDE_SCRATCH_WRITE"
    NETWORK_EGRESS = "NETWORK_EGRESS"
    SPEND = "SPEND"
    DEVICE_COMMAND = "DEVICE_COMMAND"
    IRREVERSIBLE = "IRREVERSIBLE"


class CheckKind(Enum):
    """The rungs of the check ladder, cheapest first."""

    SCHEMA = "SCHEMA"
    LINT = "LINT"
    TYPES = "TYPES"
    ALLOWLIST = "ALLOWLIST"
    SIZE_CAP = "SIZE_CAP"
    SANDBOX_TESTS = "SANDBOX_TESTS"
    JUDGE = "JUDGE"  # A judge model on its own slot.
    HUMAN = "HUMAN"  # The operator, reporting through the Warden.


class CheckOutcome(Enum):
    """How one rung of the ladder ended."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"  # Judge and human only.


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


class ProposalSubmitted(WaggleMessage):
    """Propose an action with a side effect to the Capping gate (capping.proposal_submitted).

    A request from a Worker to its Warden: what it will do, its risk tier, the postconditions it
    expects to hold afterwards, the task's Tempo and why. The reply is the gate's Verdict.
    """

    task_id: TaskIdField = Field(description="The task the action serves.")
    cell_id: CellIdField = Field(description="The Cell the action runs on.")
    proposer: WorkerIdField = Field(
        description="The bee proposing. Equals the envelope sender (receiver rule), and the "
        "Warden rejects a capping.check_result whose sender is the proposer (receiver rule).",
    )
    risk_tier: RiskTier = Field(
        description="The declared tier; checks may raise it, never lower it."
    )
    action: ProposedAction = Field(description="The diff, command or sequence.")
    postconditions: tuple[Postcondition, ...] = Field(
        max_length=MAX_POSTCONDITIONS,
        description="Assertions stated before acting; may be empty only for READ_ONLY.",
    )
    tempo: Tempo = Field(
        description="The task's Tempo as the proposer knows it; the Warden takes the Tempo from "
        "its own TaskAssign for task_id and rejects a differing one (receiver rule), so a bee "
        "never shortens its own check ladder.",
    )
    spend_estimate: float = Field(
        default=DEFAULT_SPEND_ESTIMATE,
        ge=MIN_SPEND_ESTIMATE,
        description="Expected spend in the manifest's currency; non-zero exactly for the SPEND "
        "tier.",
    )
    clearance: HoneyClearance = Field(
        description="The label of the action's text, from the task's clearance."
    )
    reason: _Reason = Field(description="Why the bee wants to do this.")

    @model_validator(mode="after")
    def _postconditions_match_tier_and_fit(self) -> ProposalSubmitted:
        """Require a postcondition above READ_ONLY and bound their text in total."""
        # A side effect with nothing to verify afterwards could never be rolled back on failure,
        # because failure could never be observed; only a read has nothing to check.
        if not self.postconditions and self.risk_tier is not RiskTier.READ_ONLY:
            raise ValueError(
                f"A {self.risk_tier.value} proposal must state at least one postcondition; only "
                "READ_ONLY may state none."
            )
        # The per-item caps allow far more than a frame holds, so the sum is bounded too.
        total = sum(_postcondition_chars(postcondition) for postcondition in self.postconditions)
        if total > MAX_POSTCONDITIONS_CHARS:
            raise ValueError(
                f"A proposal's postconditions carry at most {MAX_POSTCONDITIONS_CHARS} "
                f"characters in total, got {total}."
            )
        return self

    @model_validator(mode="after")
    def _spend_exactly_for_spend_tier(self) -> ProposalSubmitted:
        """Require a non-zero estimate for the SPEND tier and zero for every other."""
        # A SPEND proposal estimating nothing would dodge the spend ladder; any other tier
        # estimating something is spending under a tier that never checks spend.
        spending = self.risk_tier is RiskTier.SPEND
        if spending != (self.spend_estimate != MIN_SPEND_ESTIMATE):
            raise ValueError(
                f"ProposalSubmitted spend_estimate is non-zero exactly for the SPEND tier, got "
                f"tier {self.risk_tier.value} with spend_estimate {self.spend_estimate}."
            )
        return self


class CheckResult(WaggleMessage):
    """Report one rung of the check ladder for a proposal (capping.check_result, an event).

    From a checker to the Warden running the gate, which forwards it to the Queen for the trail.
    """

    proposal_id: MessageIdField = Field(description="The proposal this check belongs to.")
    task_id: TaskIdField = Field(
        description="The task, so the trail links without the proposal record."
    )
    cell_id: CellIdField = Field(
        description="The Cell the proposal runs on, so the Queen's trail writer can key the "
        "event to a NIGHT_VEIL Cell's ephemeral segment without a lookup.",
    )
    check: CheckKind = Field(description="Which rung ran.")
    outcome: CheckOutcome = Field(description="The result.")
    reason: _Reason = Field(description="The checker's one-line reason.")
    detail: str = Field(
        default="",
        max_length=MAX_DETAIL_CHARS,
        description="Bounded findings: lint output, failing test names, the judge's structured "
        "reasons.",
    )
    clearance: HoneyClearance = Field(description="The label of detail.")
    duration_s: float = Field(ge=MIN_DURATION_S, description="Wall-clock seconds.")


def _postcondition_chars(postcondition: Postcondition) -> int:
    """Count the characters one Postcondition carries across its text fields."""
    # The kind is an enum member, not text the wire grows with, so it is not counted.
    return (
        len(postcondition.subject)
        + sum(len(item) for item in postcondition.argv)
        + len(postcondition.expected or "")
    )
