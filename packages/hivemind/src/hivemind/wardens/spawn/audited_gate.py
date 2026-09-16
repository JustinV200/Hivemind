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

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. Built by `hivemind.wardens.spawn.spawn._build_capping_gate` in place of a plain
    `CappingGate`, from the Warden's own `judge_reviewer`, `judge_rubrics`, `audit_sampler`,
    `findings_sink` and `audit_rates` (`hivemind.wardens.deps.WardenDeps`, roadmap step 4.10's own
    additive fields). Calls into `hivemind.supervision.capping` (AuditDeps, AuditRates,
    AuditSampler, FindingsSink, GateDeps, GateOutcome, JudgeReviewer, JudgeRubric, ProposalState,
    RiskTier, TierSpec, audit_completed) and waggle only.

Key invariants:
    - `run` always returns exactly what `CappingGate.run` returned; auditing is a side effect that
      never changes the proposal's own outcome, and never raises for an ordinary REJECT verdict
      (`audit_completed`'s own "Key invariants").
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
    JudgeReviewer,
    JudgeRubric,
    ProposalState,
    RiskTier,
    TierSpec,
    audit_completed,
)
from hivemind.supervision.capping.lease_view import LeaseView
from waggle.ids import MessageId

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
        self, proposal_id: MessageId, capabilities: CapabilitySet, lease: LeaseView
    ) -> GateOutcome:
        """Walk `proposal_id` through `CappingGate.run`, then sample it for audit if terminal.

        Args and Returns mirror `CappingGate.run` exactly; see its own docstring.
        """
        outcome = await super().run(proposal_id, capabilities, lease)
        if outcome.state not in _AUDITABLE_OUTCOMES:
            return outcome  # REJECTED: nothing completed to sample (module docstring).
        tier = self._deps.tiers.tiers.get(self.get(proposal_id).risk_tier)
        if tier is None:
            return outcome  # Defensive: an unconfigured tier never reaches a terminal outcome.
        await self._audit_if_sampled(proposal_id, tier)
        return outcome

    async def _audit_if_sampled(self, proposal_id: MessageId, tier: TierSpec) -> None:
        """Run `audit_completed` for the now-terminal proposal named by `proposal_id`."""
        proposal = self.get(proposal_id)
        await audit_completed(self._audit, proposal, tier, self._rates)
