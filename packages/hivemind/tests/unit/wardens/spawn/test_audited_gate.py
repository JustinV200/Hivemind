"""Tests for hivemind.wardens.spawn.audited_gate: AuditingCappingGate.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/audited_gate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.audited_gate for AuditingCappingGate and AuditWiring, the classes
      under test.
    - tests.unit.supervision.capping.test_gate for the plain CappingGate pattern this mirrors.
"""

from __future__ import annotations

from pathlib import Path

from builders.capping import (
    FakeLeaseView,
    RepeatingJudgeReviewer,
    make_judge_verdict,
    make_proposal,
)
from builders.cells import make_cell, make_identity

from hivemind.cell import CellKind, FakeSession, NoopSnapshotter
from hivemind.guard import CapabilitySet
from hivemind.pheromone import AlarmEvent, MemoryPheromoneTrail, TrailQuery
from hivemind.supervision.capping import (
    AuditRates,
    AuditSampler,
    InMemoryFindingsSink,
    JudgeOutcome,
)
from hivemind.supervision.capping.checks.deterministic import deterministic_checks
from hivemind.supervision.capping.checks.rubrics import load_judge_rubrics
from hivemind.supervision.capping.gate import GateDeps
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable
from hivemind.wardens.spawn.audited_gate import AuditingCappingGate, AuditWiring
from waggle.clock import FakeClock
from waggle.messages.capping import CheckKind
from waggle.messages.supervision import AlarmKind

# A generous rate: AuditSampler.should_sample's own deterministic hash always samples at 1.0
# (its own docstring), so every test here proves the sampled path without a retry loop.
_ALWAYS_SAMPLED_RATE = 1.0


def _tiers(audit_rate: float) -> TierTable:
    return TierTable(
        tiers={
            RiskTier.SCRATCH_WRITE: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.SIZE_CAP),
                floor=(CheckKind.SCHEMA,),
                snapshot_before=False,
                max_diff_bytes=1_048_576,
                judge=False,  # Ungated in real time; audit_rate is what samples it.
                audit_rate=audit_rate,
            ),
        }
    )


def _build_gate(
    tmp_path: Path, *, audit_rate: float, reviewer: RepeatingJudgeReviewer
) -> tuple[AuditingCappingGate, MemoryPheromoneTrail, AuditRates, Path]:
    """Build an AuditingCappingGate over fakes, plus the trail, rates and scratch dir."""
    scratch_root = tmp_path / "scratch"
    clock = FakeClock()
    session = FakeSession(scratch_root, clock)
    trail = MemoryPheromoneTrail(clock)
    deps = GateDeps(
        session=session,
        snapshotter=NoopSnapshotter(),
        cell=make_cell(kind=CellKind.REAL, clock=clock),
        tiers=_tiers(audit_rate),
        trail=trail,
        identity=make_identity(clock),
        clock=clock,
        checks=deterministic_checks(),
    )
    rates = AuditRates()
    wiring = AuditWiring(
        reviewer=reviewer,
        rubrics=load_judge_rubrics(),
        sampler=AuditSampler(),
        sink=InMemoryFindingsSink(),
        rates=rates,
    )
    return AuditingCappingGate(deps, wiring), trail, rates, scratch_root


async def test_a_sampled_verified_proposal_records_capping_audited(tmp_path: Path) -> None:
    reviewer = RepeatingJudgeReviewer(make_judge_verdict(outcome=JudgeOutcome.APPROVE))
    gate, trail, rates, scratch_root = _build_gate(
        tmp_path, audit_rate=_ALWAYS_SAMPLED_RATE, reviewer=reviewer
    )
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    lease = FakeLeaseView(scratch_root)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.VERIFIED
    assert len(reviewer.calls) == 1  # The judge actually reviewed the now-terminal proposal.
    events = await trail.query(TrailQuery(subject_id=proposal_id))
    assert "capping.audited" in [event.kind for event in events]
    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 1
    assert rates.failed(RiskTier.SCRATCH_WRITE) == 0


async def test_a_sampled_proposal_the_judge_rejects_raises_an_audit_failed_alarm(
    tmp_path: Path,
) -> None:
    reviewer = RepeatingJudgeReviewer(
        make_judge_verdict(outcome=JudgeOutcome.REJECT, reasons=("Touches an undeclared path.",))
    )
    gate, trail, rates, scratch_root = _build_gate(
        tmp_path, audit_rate=_ALWAYS_SAMPLED_RATE, reviewer=reviewer
    )
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    lease = FakeLeaseView(scratch_root)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    # The audit is a side effect: it never changes the proposal's own gate outcome.
    assert outcome.state is ProposalState.VERIFIED
    assert rates.failed(RiskTier.SCRATCH_WRITE) == 1
    events = await trail.query(TrailQuery())
    alarm_events = [event for event in events if isinstance(event, AlarmEvent)]
    assert any(event.payload.get("kind") == AlarmKind.AUDIT_FAILED.value for event in alarm_events)


async def test_a_zero_rate_tier_never_samples_or_calls_the_judge(tmp_path: Path) -> None:
    reviewer = RepeatingJudgeReviewer()
    gate, trail, rates, scratch_root = _build_gate(tmp_path, audit_rate=0.0, reviewer=reviewer)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    lease = FakeLeaseView(scratch_root)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.VERIFIED
    assert reviewer.calls == []
    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 0
    events = await trail.query(TrailQuery(subject_id=proposal_id))
    assert "capping.audited" not in [event.kind for event in events]
