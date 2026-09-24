"""Define AuditingCappingGate: sample a terminal proposal for after-the-fact judge review.

Roadmap step 4.10: "for tiers the table marks as not gated in real time, sample completed work at
a per-tier rate, review it with the judge after the fact, deposit findings as Nectar, raise an
Alarm on a failed audit." `hivemind.supervision.capping.audit.audit_completed` is that whole rule;
this module is the one place it is actually called from, because a `Proposal` reaches its terminal
state (`VERIFIED` or `ROLLED_BACK`) inside `hivemind.supervision.capping.gate.CappingGate.run`
itself, and that module is outside this phase's own file list. `AuditingCappingGate` subclasses
`CappingGate` and overrides `run` to call `super().run()` first, then `audit_completed` once the
outcome is terminal -- the same "wrap, don't fork" shape `hivemind.wardens.spawn.spawn._build_
capping_gate` already uses everywhere else in this package (a fresh `CappingGate` per sub-bee).
One kind of proposal is not sampled but always judged, right after it is applied (ADR-0032,
"Judges read recordings"): a VERIFIED `irreversible` GUI proposal, reviewed with the evidence its
GUI surface recorded, before the tool returns, so the bee's next step waits on the verdict. The
verdict rides back on `GateOutcome.review`, and the Worker's tool raises the one Alarm a REJECT
calls for, on the escalation path that ends the attempt. Every other GUI proposal that is sampled
is audited with the same evidence.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. Built by `hivemind.wardens.spawn.spawn._build_capping_gate` in place of a plain
    `CappingGate`, from the Warden's own `judge_reviewer`, `judge_rubrics`, `audit_sampler`,
    `findings_sink` and `audit_rates` (`hivemind.wardens.deps.WardenDeps`, roadmap step 4.10's own
    additive fields). Calls into `hivemind.supervision.capping` (AuditDeps, AuditRates,
    AuditSampler, FindingsSink, GateDeps, GateOutcome, JudgeEvidence, JudgeReviewer, JudgeRubric,
    Proposal, ProposalState, RiskTier, audit_completed, review_applied, and the gate's GuiSurface
    for evidence) and waggle only.

Key invariants:
    - `run` returns what `CappingGate.run` returned, with `review` set only for a judged
      irreversible GUI proposal; auditing never changes the proposal's own state, and never
      raises for an ordinary REJECT verdict (`audit_completed`'s own "Key invariants").
    - Auditing only ever runs for a terminal outcome with a configured tier: a proposal whose own
      `risk_tier` has no `TierSpec` at all (an unconfigured tier, already REJECTED by `_check_and_
      cap`) is never sampled, since there is no `audit_rate` to sample it at.

See Also:
    - .claude/codingrules.md section 8.12 for "what cannot be gated is sampled".
    - .claude/roadmap.md step 4.10 for this module's own deliverable, verbatim.
    - hivemind.supervision.capping.audit for AuditSampler and audit_completed, this module's one
      collaborator pair.
    - hivemind.wardens.spawn.spawn for _build_capping_gate, this class's one caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.guard import CapabilitySet
from hivemind.supervision.capping import (
    AuditDeps,
    AuditRates,
    AuditSampler,
    CappingGate,
    FindingsSink,
    GateDeps,
    GateOutcome,
    JudgeEvidence,
    JudgeReviewer,
    JudgeRubric,
    Proposal,
    ProposalState,
    RiskTier,
    audit_completed,
    review_applied,
)
from hivemind.supervision.capping.lease_view import LeaseView
from hivemind.supervision.capping.leave import Asker
from waggle.ids import MessageId
from waggle.messages.capping import ActionKind

# The two terminal outcomes audit_completed's own module docstring calls "already-terminal
# (VERIFIED or ROLLED_BACK)"; REJECTED (never applied at all) is never sampled, since there is
# nothing "completed" to review.
_AUDITABLE_OUTCOMES = frozenset({ProposalState.VERIFIED, ProposalState.ROLLED_BACK})

__all__ = ["AuditWiring", "AuditingCappingGate"]


@dataclass(frozen=True, slots=True)
class AuditWiring:
    """The Warden's own audit collaborators, bundled (codingrules 5.1's parameter-count limit).

    One value of `hivemind.wardens.deps.WardenDeps`'s own `judge_reviewer`/`judge_rubrics`/
    `audit_sampler`/`findings_sink`/`audit_rates` fields, gathered at `AuditingCappingGate`'s one
    call site (`hivemind.wardens.spawn.spawn._build_capping_gate`).
    """

    reviewer: JudgeReviewer  # WardenDeps.judge_reviewer.
    rubrics: dict[RiskTier, JudgeRubric]  # WardenDeps.judge_rubrics.
    sampler: AuditSampler  # WardenDeps.audit_sampler.
    sink: FindingsSink  # WardenDeps.findings_sink.
    rates: AuditRates  # WardenDeps.audit_rates.


class AuditingCappingGate(CappingGate):
    """A CappingGate whose `run` also samples a terminal proposal for after-the-fact review."""

    def __init__(self, deps: GateDeps, wiring: AuditWiring) -> None:
        """Build an AuditingCappingGate over `deps`, with the Warden's own audit collaborators.

        Args:
            deps: The same GateDeps a plain `CappingGate` is built with.
            wiring: The Warden's own judge reviewer, rubrics, sampler, findings sink and rates.
        """
        super().__init__(deps)
        self._audit = AuditDeps(
            sampler=wiring.sampler,
            reviewer=wiring.reviewer,
            rubrics=wiring.rubrics,
            sink=wiring.sink,
            trail=deps.trail,
            identity=deps.identity,
            clock=deps.clock,
        )
        self._rates = wiring.rates

    async def run(
        self,
        proposal_id: MessageId,
        capabilities: CapabilitySet,
        lease: LeaseView,
        asker: Asker | None = None,
    ) -> GateOutcome:
        """Walk `proposal_id` through `CappingGate.run`, then sample it for audit if terminal.

        Args and Returns mirror `CappingGate.run` exactly; see its own docstring.
        """
        outcome = await super().run(proposal_id, capabilities, lease, asker)
        if outcome.state not in _AUDITABLE_OUTCOMES:
            return outcome  # REJECTED: nothing completed to sample (module docstring).
        proposal = self.get(proposal_id)
        tier = self._deps.tiers.tiers.get(proposal.risk_tier)
        if tier is None:
            return outcome  # Defensive: an unconfigured tier never reaches a terminal outcome.
        evidence = await self._evidence(proposal)
        if _judged_now(proposal, outcome):
            verdict = await review_applied(self._audit, proposal, evidence)
            return outcome.model_copy(update={"review": verdict})
        await audit_completed(self._audit, proposal, tier, self._rates, evidence)
        return outcome

    async def _evidence(self, proposal: Proposal) -> JudgeEvidence | None:
        """What the GUI surface recorded for a GUI proposal; None for any other kind."""
        gui = self._deps.gui
        if gui is None or proposal.action.kind is not ActionKind.GUI:
            return None
        return await gui.evidence(proposal)


def _judged_now(proposal: Proposal, outcome: GateOutcome) -> bool:
    """Whether `proposal` is judged now rather than sampled: an applied irreversible GUI action."""
    return (
        outcome.state is ProposalState.VERIFIED
        and proposal.risk_tier is RiskTier.IRREVERSIBLE
        and proposal.action.kind is ActionKind.GUI
    )
