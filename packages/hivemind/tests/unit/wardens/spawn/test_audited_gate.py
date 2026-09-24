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

from dataclasses import dataclass, field
from pathlib import Path

from builders.capping import (
    FakeLeaseView,
    RepeatingJudgeReviewer,
    make_action,
    make_judge_verdict,
    make_postcondition,
    make_proposal,
)
from builders.cells import make_cell, make_identity
from builders.gui import ScriptedSurface

from hivemind.cell import CellKind, FakeSession, NoopSnapshotter
from hivemind.guard import CapabilitySet
from hivemind.pheromone import AlarmEvent, MemoryPheromoneTrail, TrailQuery
from hivemind.supervision.capping import (
    AuditRates,
    AuditSampler,
    GateOutcome,
    InMemoryFindingsSink,
    JudgeEvidence,
    JudgeOutcome,
    Proposal,
)
from hivemind.supervision.capping.checks.deterministic import deterministic_checks
from hivemind.supervision.capping.checks.rubrics import load_judge_rubrics
from hivemind.supervision.capping.gate import GateDeps
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable
from hivemind.wardens.spawn.audited_gate import AuditingCappingGate, AuditWiring
from waggle.clock import FakeClock
from waggle.messages.capping import ActionKind, CheckKind, ElementTarget, GuiOp, GuiStep
from waggle.messages.labels import PostconditionKind
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


# ──────────────────────────────────────────────────────────────────────────────
# GUI proposals: judged right after apply when irreversible, else sampled (ADR-0032)
# ──────────────────────────────────────────────────────────────────────────────

_PAY = GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Pay"))
_PAID = make_postcondition(PostconditionKind.URL_MATCHES, subject="page", expected="file:///paid*")
_EVIDENCE = JudgeEvidence(text="Screens: before, after\nAfter: url=file:///paid", frames=(b"p",))
_ONLY_SCHEMA = TierSpec(checks=(CheckKind.SCHEMA,), floor=(CheckKind.SCHEMA,), judge=False)


def _gui_proposal(tier: RiskTier) -> Proposal:
    action = make_action(ActionKind.GUI, gui=(_PAY,), steps=())
    return make_proposal(risk_tier=tier, action=action, postconditions=(_PAID,))


@dataclass(frozen=True)
class _Audit:
    """The audit side of one `_run_gui` gate: who judges, how often, against what goal."""

    reviewer: RepeatingJudgeReviewer = field(default_factory=RepeatingJudgeReviewer)
    audit_rate: float = 0.0  # Rate 0 samples nothing: only an irreversible GUI action is judged.
    goal: str | None = None


async def _run_gui(
    tmp_path: Path, proposal: Proposal, surface: ScriptedSurface, audit: _Audit | None = None
) -> tuple[GateOutcome, RepeatingJudgeReviewer, MemoryPheromoneTrail]:
    """Run `proposal` through an AuditingCappingGate whose GateDeps carry `surface`."""
    audit = audit if audit is not None else _Audit()
    reviewer, rate = audit.reviewer, audit.audit_rate
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    tiers = TierTable(
        tiers={
            RiskTier.SCRATCH_WRITE: _ONLY_SCHEMA.model_copy(update={"audit_rate": rate}),
            RiskTier.IRREVERSIBLE: _ONLY_SCHEMA.model_copy(update={"audit_rate": rate}),
        }
    )
    deps = GateDeps(
        session=FakeSession(tmp_path, clock),
        snapshotter=NoopSnapshotter(),
        cell=make_cell(kind=CellKind.REAL, clock=clock),
        tiers=tiers,
        trail=trail,
        identity=make_identity(clock),
        clock=clock,
        checks=deterministic_checks(),
        gui=surface,
    )
    wires = AuditWiring(
        reviewer=reviewer,
        rubrics=load_judge_rubrics(),
        sampler=AuditSampler(),
        sink=InMemoryFindingsSink(),
        rates=AuditRates(),
        goal=audit.goal,
    )
    gate = AuditingCappingGate(deps, wires)
    await gate.propose(proposal)
    outcome = await gate.run(proposal.id, CapabilitySet.parse(), FakeLeaseView(tmp_path))
    return outcome, reviewer, trail


async def test_an_applied_irreversible_gui_action_is_judged_with_its_evidence(
    tmp_path: Path,
) -> None:
    surface = ScriptedSurface(evidence=_EVIDENCE)

    outcome, reviewer, _ = await _run_gui(
        tmp_path, _gui_proposal(RiskTier.IRREVERSIBLE), surface, _Audit(goal="Pay invoice 42 only.")
    )

    # Rate 0 samples nothing, yet the judge ran: irreversible GUI work is always judged, and
    # against the task's own goal.
    assert outcome.state is ProposalState.VERIFIED
    assert outcome.review is not None and outcome.review.outcome is JudgeOutcome.APPROVE
    assert reviewer.calls[0].evidence == _EVIDENCE
    assert reviewer.calls[0].goal == "Pay invoice 42 only."
    assert reviewer.calls[0].rubric.rubric_id == "irreversible-v1"
    assert surface.calls[-2:] == ["finish VERIFIED None", "evidence"]  # Recorded, then judged.


async def test_a_rejected_irreversible_gui_action_comes_back_with_its_verdict(
    tmp_path: Path,
) -> None:
    verdict = make_judge_verdict(JudgeOutcome.REJECT, reasons=("Paid the wrong invoice.",))
    reviewer = RepeatingJudgeReviewer(verdict)

    outcome, _, trail = await _run_gui(
        tmp_path, _gui_proposal(RiskTier.IRREVERSIBLE), ScriptedSurface(), _Audit(reviewer)
    )

    # The state stays VERIFIED (nothing can undo it); the verdict tells the tool to stop, and the
    # tool raises the Alarm (test_proposals), so the gate raises none of its own.
    assert outcome.state is ProposalState.VERIFIED
    assert outcome.review == verdict
    events = await trail.query(TrailQuery())
    assert not [event for event in events if isinstance(event, AlarmEvent)]
    assert "capping.audited" in [event.kind for event in events]


async def test_a_rolled_back_irreversible_gui_action_is_not_judged_after_apply(
    tmp_path: Path,
) -> None:
    surface = ScriptedSurface(holds=False)

    outcome, reviewer, _ = await _run_gui(tmp_path, _gui_proposal(RiskTier.IRREVERSIBLE), surface)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert outcome.review is None
    assert reviewer.calls == []  # Its failed postcondition already escalates it.


async def test_a_sampled_gui_action_is_audited_with_the_same_evidence(tmp_path: Path) -> None:
    surface = ScriptedSurface(evidence=_EVIDENCE)

    outcome, reviewer, _ = await _run_gui(
        tmp_path, _gui_proposal(RiskTier.SCRATCH_WRITE), surface, _Audit(audit_rate=1.0)
    )

    assert outcome.review is None  # Sampled audits never hold the tool up.
    assert reviewer.calls[0].evidence == _EVIDENCE


async def test_an_irreversible_proposal_that_is_not_gui_is_only_sampled(tmp_path: Path) -> None:
    proposal = make_proposal(risk_tier=RiskTier.IRREVERSIBLE)  # A scratch diff.

    outcome, reviewer, _ = await _run_gui(tmp_path, proposal, ScriptedSurface())

    assert outcome.state is ProposalState.VERIFIED
    assert outcome.review is None
    assert reviewer.calls == []
