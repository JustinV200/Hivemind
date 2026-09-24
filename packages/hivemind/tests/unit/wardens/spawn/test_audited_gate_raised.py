"""Tests for hivemind.wardens.spawn.audited_gate: a Guard Bee raise takes effect in the gate.

Roadmap step 10.6: the Guard Bee raises a Capping tier's sampled-audit rate by recording
`guard.audit_rate_raised` on the Queen's trail, and a Warden whose gate records to that same trail
(the Hive Stand's) samples at the higher of the tier table's rate and the live raise, from the
next terminal proposal on. An expired raise leaves the table's rate, and a raise never lowers it.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/audited_gate.py (codingrules section 3), split by feature
    from test_audited_gate.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.capping.audit for AuditRateRaise and raised_audit_rate.
    - tests.unit.wardens.spawn.test_audited_gate for the gate's own sampling path.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from builders.capping import (
    FakeLeaseView,
    RepeatingJudgeReviewer,
    make_judge_verdict,
    make_proposal,
)
from builders.cells import make_cell, make_identity

from hivemind.cell import CellKind, FakeSession, NoopSnapshotter
from hivemind.guard import CapabilitySet, new_guard_report_id
from hivemind.pheromone import GuardEvent, MemoryPheromoneTrail
from hivemind.supervision.capping import (
    AUDIT_RATE_RAISED_KIND,
    AuditRateRaise,
    AuditRates,
    AuditSampler,
    InMemoryFindingsSink,
    JudgeOutcome,
)
from hivemind.supervision.capping.checks.deterministic import deterministic_checks
from hivemind.supervision.capping.checks.rubrics import load_judge_rubrics
from hivemind.supervision.capping.gate import GateDeps
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable
from hivemind.wardens.spawn.audited_gate import AuditingCappingGate, AuditWiring
from waggle.clock import FakeClock
from waggle.ids import new_event_id
from waggle.messages.capping import CheckKind

_HOLD = timedelta(hours=1)  # How long the raise in these tests lasts.


def _gate(
    tmp_path: Path, clock: FakeClock, trail: MemoryPheromoneTrail, table_rate: float
) -> tuple[AuditingCappingGate, RepeatingJudgeReviewer]:
    """Build a gate whose SCRATCH_WRITE tier samples at `table_rate`, recording to `trail`."""
    spec = TierSpec(
        checks=(CheckKind.SCHEMA, CheckKind.SIZE_CAP),
        floor=(CheckKind.SCHEMA,),
        max_diff_bytes=1_048_576,
        judge=False,
        audit_rate=table_rate,
    )
    deps = GateDeps(
        session=FakeSession(tmp_path / "scratch", clock),
        snapshotter=NoopSnapshotter(),
        cell=make_cell(kind=CellKind.REAL, clock=clock),
        tiers=TierTable(tiers={RiskTier.SCRATCH_WRITE: spec}),
        trail=trail,
        identity=make_identity(clock),
        clock=clock,
        checks=deterministic_checks(),
    )
    reviewer = RepeatingJudgeReviewer(make_judge_verdict(outcome=JudgeOutcome.APPROVE))
    wiring = AuditWiring(
        reviewer=reviewer,
        rubrics=load_judge_rubrics(),
        sampler=AuditSampler(),
        sink=InMemoryFindingsSink(),
        rates=AuditRates(),
    )
    return AuditingCappingGate(deps, wiring), reviewer


async def _record_raise(
    trail: MemoryPheromoneTrail, clock: FakeClock, to_rate: float, from_rate: float = 0.0
) -> None:
    """Record the Guard Bee's raise of SCRATCH_WRITE to `to_rate`, for one hour from now."""
    raised = AuditRateRaise(
        tier=RiskTier.SCRATCH_WRITE,
        from_rate=from_rate,
        to_rate=to_rate,
        until=clock.now() + _HOLD,
        report_id=new_guard_report_id(clock),
        rule="audit_failure_rate",
    )
    identity = make_identity(clock)
    event = GuardEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind=AUDIT_RATE_RAISED_KIND,
        subject_id=identity.hive_id,
        payload=raised.to_payload(),
    )
    await trail.record(event)


async def _run_one(gate: AuditingCappingGate, tmp_path: Path) -> None:
    """Propose and run one SCRATCH_WRITE proposal to VERIFIED."""
    proposal_id = await gate.propose(make_proposal(risk_tier=RiskTier.SCRATCH_WRITE))
    await gate.run(proposal_id, CapabilitySet.parse(), FakeLeaseView(tmp_path / "scratch"))


async def test_a_live_raise_samples_what_the_tier_table_alone_never_would(tmp_path: Path) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    gate, reviewer = _gate(tmp_path, clock, trail, table_rate=0.0)
    await _run_one(gate, tmp_path)
    before = len(reviewer.calls)

    await _record_raise(trail, clock, to_rate=1.0)
    await _run_one(gate, tmp_path)

    assert before == 0  # The table's 0.0 samples nothing.
    assert len(reviewer.calls) == 1  # The raise took effect on the very next proposal.


async def test_an_expired_raise_leaves_the_tier_tables_rate(tmp_path: Path) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    gate, reviewer = _gate(tmp_path, clock, trail, table_rate=0.0)
    await _record_raise(trail, clock, to_rate=1.0)
    clock.advance(_HOLD.total_seconds() + 1.0)

    await _run_one(gate, tmp_path)

    assert reviewer.calls == []


async def test_a_raise_never_lowers_the_tier_tables_own_rate(tmp_path: Path) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    gate, reviewer = _gate(tmp_path, clock, trail, table_rate=1.0)
    await _record_raise(trail, clock, to_rate=0.01)

    await _run_one(gate, tmp_path)

    assert len(reviewer.calls) == 1  # Still sampled at the table's 1.0, not the raise's 0.01.
