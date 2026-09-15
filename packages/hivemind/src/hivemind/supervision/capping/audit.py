"""Define AuditSampler, audit_completed and AuditRates: the after-the-fact half of Capping.

Codingrules section 8.12: "What cannot be gated is sampled. Tiers the table marks as ungated in
real time are audited after the fact by the judge; findings become Nectar and Alarms, and the
Guard Bee raises a tier's rate when its failure rate climbs." A tier whose `TierSpec.judge` is
False never sees `CheckKind.JUDGE` in its real-time ladder (`hivemind.supervision.capping.tiers.
checks_for`); this module is the fallback for exactly those tiers. Once a proposal has already
reached a terminal state (`VERIFIED` or `ROLLED_BACK`), `AuditSampler` decides -- deterministically
by default, so a test never needs retries -- whether this one is sampled at its tier's
`TierSpec.audit_rate`, and `audit_completed` runs the same `JudgeReviewer` the real-time `JUDGE`
check would have used, deposits the finding through a `FindingsSink` (Nectar, the Hive's raw
intake before Honey ripens it -- the Honey Store itself is phase 7, so this module only defines the
seam and ships an in-memory implementation), records a `capping.audited` trail event, and raises an
`Alarm` of kind `AUDIT_FAILED` on a `REJECT` verdict, through the same `record_alarm_event` path
every other supervisor uses. `AuditRates` is the read model the Guard Bee (phase 10, part of
`hivemind.guard`, the Hive's policy engine) will read to raise a tier's `audit_rate` when its
failure rate climbs; this module only counts, it does not decide.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Called
    by whichever composition root schedules audit sampling (a later dispatch's Warden tick, or
    `hive capping audit --sample`, roadmap step 4.11) once a proposal reaches a terminal state.
    Calls into `hivemind.cell` (CellIdentity), `hivemind.pheromone` (CappingEvent, PheromoneTrail),
    `hivemind.supervision.alarm`, `hivemind.supervision.alarm_trail`, `hivemind.supervision.
    capping.checks.judge`, `.checks.rubrics`, `.errors`, `.proposal`, `.tiers` and waggle only;
    never `hivemind.memory` or `hivemind.honey_store` (`FindingsSink` is the seam a later phase
    implements against those, not an import this package takes on itself).

Key invariants:
    - AuditSampler.should_sample is a pure function of (proposal_id, tier, audit_rate) unless a
      `random.Random` was injected; the same proposal id always samples the same way for a given
      tier and rate, so tests never need a fixed seed or a retry loop.
    - audit_completed never raises for an ordinary REJECT verdict: that path deposits a finding and
      raises an Alarm, it does not propagate an exception. It raises CappingError only when the
      sampled tier has no configured rubric at all.
    - The capping.audited trail event's payload carries only the tier, the verdict outcome and the
      rubric id -- never JudgeVerdict.reasons or .notes (codingrules section 12: no event ever
      carries text).

See Also:
    - .claude/codingrules.md section 8.12 for "What cannot be gated is sampled."
    - .claude/codingrules.md section 12 for the payload-carries-no-text rule capping.audited
      follows.
    - hivemind.supervision.capping.tiers for checks_for, the real-time counterpart this module's
      sampling backs up.
    - hivemind.supervision.capping.checks.judge for JudgeReviewer, JudgeRequest and JudgeVerdict.
    - hivemind.supervision.alarm_trail for record_alarm_event, the alarm.* trail path this module
      reuses rather than inventing its own.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from random import Random
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CellIdentity
from hivemind.pheromone import CappingEvent, PheromoneTrail
from hivemind.supervision.alarm import Alarm, AlarmKind, AlarmSeverity, AlarmState
from hivemind.supervision.alarm_trail import record_alarm_event
from hivemind.supervision.capping.checks.judge import (
    JudgeOutcome,
    JudgeRequest,
    JudgeReviewer,
    JudgeVerdict,
)
from hivemind.supervision.capping.checks.rubrics import JudgeRubric
from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.tiers import RiskTier, TierSpec
from waggle.clock import Clock
from waggle.ids import MessageId, new_alarm_id, new_event_id
from waggle.messages.base import UtcDatetime
from waggle.messages.supervision import AlarmContext

__all__ = [
    "AuditDeps",
    "AuditFinding",
    "AuditRates",
    "AuditSampler",
    "FindingsSink",
    "InMemoryFindingsSink",
    "audit_completed",
]


class AuditFinding(BaseModel):
    """One post-hoc audit's result, deposited as Nectar (the Hive's raw intake) via FindingsSink."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: MessageId = Field(description="The audited proposal.")
    risk_tier: RiskTier = Field(description="Its risk tier.")
    verdict: JudgeVerdict = Field(description="The judge's structured verdict.")
    recorded_at: UtcDatetime = Field(description="When this finding was recorded.")


class FindingsSink(Protocol):
    """Deposit one AuditFinding as Nectar; the Honey Store's own sink lands in phase 7.

    `InMemoryFindingsSink` below is this phase's implementation, for tests and any composition
    root that has not wired the Honey Store's Nectar intake yet.
    """

    async def deposit(self, finding: AuditFinding) -> None:
        """Deposit `finding` as Nectar (raw intake, ripened into Honey by a later pipeline).

        Args:
            finding: The audit result to deposit.
        """
        ...


class InMemoryFindingsSink:
    """An in-memory FindingsSink: appends every finding to a list, for tests and demo paths."""

    def __init__(self) -> None:
        """Build an InMemoryFindingsSink with nothing deposited yet."""
        self.findings: list[AuditFinding] = []

    async def deposit(self, finding: AuditFinding) -> None:
        """Append `finding` to `findings`.

        Args:
            finding: The audit result to deposit.
        """
        self.findings.append(finding)


class AuditSampler:
    """Decide, deterministically by default, whether one applied proposal is sampled for audit."""

    def __init__(self, rng: Random | None = None) -> None:
        """Build an AuditSampler.

        Args:
            rng: Injected randomness for production use, so a systematic proposal-id pattern
                cannot dodge sampling. None (the default, and what every test uses) makes
                `should_sample` a pure hash of the proposal id and tier instead, so the same
                proposal always samples the same way for a given rate -- no seed to manage, no
                flake from a real random draw.
        """
        self._rng = rng

    def should_sample(self, proposal_id: MessageId, tier: RiskTier, audit_rate: float) -> bool:
        """Return whether one proposal is sampled for after-the-fact audit.

        Args:
            proposal_id: The proposal under consideration.
            tier: Its risk tier, folded into the hash so the same proposal id samples
                independently at each tier (relevant only if an id were ever reused across tiers).
            audit_rate: The tier's configured sampling rate, 0.0 to 1.0.

        Returns:
            True if this proposal should be audited.
        """
        if audit_rate <= 0.0:
            return False  # A zero rate never samples, whatever the hash or draw would say.
        if self._rng is not None:
            return self._rng.random() < audit_rate
        # Deterministic path: hash proposal_id+tier to a stable fraction in [0, 1). The same
        # proposal always lands on the same fraction, so a test asserts sampling with no retries.
        digest = hashlib.sha256(f"{tier.value}:{proposal_id}".encode()).digest()
        fraction = int.from_bytes(digest[:8], "big") / 2**64
        return fraction < audit_rate


class AuditRates:
    """Per-tier sampled/failed counts and the current failure rate; the Guard Bee's read model.

    Owns its own mutable counters (codingrules section 8.5, documented): `record_sample` is the
    only way they change, so every update is auditable by construction. Phase 10 (the Guard Bee,
    part of `hivemind.guard`) reads `failure_rate` to raise a tier's `audit_rate` when it climbs;
    this class does not do that itself, it only counts.
    """

    def __init__(self) -> None:
        """Build an AuditRates with nothing sampled yet."""
        self._sampled: dict[RiskTier, int] = {}
        self._failed: dict[RiskTier, int] = {}

    def record_sample(self, tier: RiskTier, *, failed: bool) -> None:
        """Record one sampled proposal for `tier`, and whether its verdict was REJECT.

        Args:
            tier: The sampled proposal's risk tier.
            failed: Whether the judge's verdict was JudgeOutcome.REJECT.
        """
        self._sampled[tier] = self._sampled.get(tier, 0) + 1
        if failed:
            self._failed[tier] = self._failed.get(tier, 0) + 1

    def sampled(self, tier: RiskTier) -> int:
        """Return how many of `tier`'s proposals have been sampled so far."""
        return self._sampled.get(tier, 0)

    def failed(self, tier: RiskTier) -> int:
        """Return how many of `tier`'s sampled proposals were rejected on audit."""
        return self._failed.get(tier, 0)

    def failure_rate(self, tier: RiskTier) -> float:
        """Return `tier`'s audit failure rate: failed / sampled, or 0.0 with nothing sampled yet."""
        total = self.sampled(tier)
        return self.failed(tier) / total if total else 0.0


@dataclass(frozen=True, slots=True)
class AuditDeps:
    """Everything one audit_completed call needs, bundled (codingrules section 5.1)."""

    sampler: AuditSampler  # Decides whether a proposal is sampled.
    reviewer: JudgeReviewer  # The same seam JudgeCheck uses for the real-time ladder.
    rubrics: dict[RiskTier, JudgeRubric]  # From checks.rubrics.load_judge_rubrics().
    sink: FindingsSink  # Where a sampled proposal's finding is deposited.
    trail: PheromoneTrail  # Where capping.audited and alarm.raised land.
    identity: CellIdentity  # hive_id/node_id/actor stamped on every event this module records.
    clock: Clock  # Source of every minted id and timestamp.


async def audit_completed(
    deps: AuditDeps, proposal: Proposal, tier: TierSpec, rates: AuditRates
) -> JudgeVerdict | None:
    """Sample `proposal` at its tier's rate, review it if sampled, and record the outcome.

    Args:
        deps: This call's collaborators.
        proposal: An already-terminal (VERIFIED or ROLLED_BACK) proposal to consider for audit.
        tier: `proposal.risk_tier`'s TierSpec, read for `audit_rate`.
        rates: Updated with this proposal's sample, so the Guard Bee's read model stays current.

    Returns:
        The judge's verdict if `proposal` was sampled and reviewed; None if it was not sampled.

    Raises:
        CappingError: `proposal.risk_tier` has no configured JudgeRubric.
    """
    if not deps.sampler.should_sample(proposal.id, proposal.risk_tier, tier.audit_rate):
        return None  # Not sampled: nothing to review, deposit or record for this proposal.
    verdict = await _review(deps, proposal)
    rates.record_sample(proposal.risk_tier, failed=verdict.outcome is JudgeOutcome.REJECT)
    await _record_audited_event(deps, proposal, verdict)
    await deps.sink.deposit(
        AuditFinding(
            proposal_id=proposal.id,
            risk_tier=proposal.risk_tier,
            verdict=verdict,
            recorded_at=deps.clock.now(),
        )
    )
    if verdict.outcome is JudgeOutcome.REJECT:
        await _raise_audit_failed(deps, proposal, verdict)
    return verdict


async def _review(deps: AuditDeps, proposal: Proposal) -> JudgeVerdict:
    """Build a JudgeRequest from `proposal` and score it through `deps.reviewer`.

    Raises:
        CappingError: `proposal.risk_tier` has no configured rubric.
    """
    rubric = deps.rubrics.get(proposal.risk_tier)
    if rubric is None:
        # Fail closed (codingrules 8.12): an unconfigured rubric is a configuration error the
        # caller should fix, never a silent "nothing to audit".
        raise CappingError(f"No judge rubric configured for tier {proposal.risk_tier.value}.")
    request = JudgeRequest(
        risk_tier=proposal.risk_tier,
        action=proposal.action,
        acceptance_criteria=proposal.postconditions,
        rubric=rubric,
    )
    # Latency class: one model call, typically seconds to tens of seconds; audit sampling runs
    # after a proposal's own terminal state, so this await never blocks the gate itself.
    return await deps.reviewer.review(request)


async def _record_audited_event(deps: AuditDeps, proposal: Proposal, verdict: JudgeVerdict) -> None:
    """Record capping.audited: ids and enum values only, never the verdict's reasons or notes."""
    event = CappingEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="capping.audited",
        subject_id=proposal.id,
        payload={
            "tier": proposal.risk_tier.value,
            "outcome": verdict.outcome.value,
            "rubric_id": verdict.rubric_id,
        },
    )
    await deps.trail.record(event)


async def _raise_audit_failed(deps: AuditDeps, proposal: Proposal, verdict: JudgeVerdict) -> None:
    """Raise an AUDIT_FAILED Alarm through the existing supervision alarm path (alarm_trail)."""
    alarm = Alarm(
        id=new_alarm_id(deps.clock),
        kind=AlarmKind.AUDIT_FAILED,
        severity=AlarmSeverity.WARNING,
        origin=proposal.proposer,
        attempts=0,
        context=AlarmContext(
            task_id=proposal.task_id,
            cell_id=proposal.cell_id,
            worker_id=proposal.proposer,
            event_id=None,
            handoff=None,
        ),
        detail="; ".join(verdict.reasons) if verdict.reasons else "audit rejected the proposal",
        clearance=proposal.clearance,
        raised_at=deps.clock.now(),
        state=AlarmState.RAISED,
    )
    # record_alarm_event is the one path every supervisor writes an alarm.* step through
    # (hivemind.supervision.alarm_trail); this raise opens the chain, the same way a Warden's own
    # send_alarm_to_queen opens one for an Alarm it mints itself.
    await record_alarm_event(deps.trail, deps.identity, deps.clock, alarm, "alarm.raised")
