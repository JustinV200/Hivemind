"""Tests for the Guard Bee's audit-rate raise, taking effect in a real Capping gate on its trail.

The Guard Bee raises a failing tier's sampled-audit rate by recording `guard.audit_rate_raised` on
the trail it reads; a Warden's auditing gate recording to that same trail (the Hive Stand's, whose
Warden shares the Queen's trail) samples at the raised rate from its very next terminal proposal,
though its own tier table still says to sample nothing.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/respond.py's raise (codingrules section 3), split
    by feature from test_respond.py because it runs a real gate.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.wardens.spawn.test_audited_gate_raised for the gate's side alone.
"""

from __future__ import annotations

from pathlib import Path

from builders.capping import (
    FakeLeaseView,
    RepeatingJudgeReviewer,
    make_judge_verdict,
    make_proposal,
)
from builders.cells import make_cell
from builders.guard_bee import GuardBeeRig, RigOptions, make_guard_bee, seed_episode

from hivemind.cell import CellKind, FakeSession, NoopSnapshotter
from hivemind.guard import CapabilitySet
from hivemind.manifest.schema.guard import GuardBeeSection, GuardSection
from hivemind.supervision.capping import (
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
from hivemind.workers.roles.guard_bee import Disposition
from waggle.messages.capping import CheckKind

# A tier that samples nothing by its own table: only a live raise can make it sample.
_UNSAMPLED = TierSpec(
    checks=(CheckKind.SCHEMA, CheckKind.SIZE_CAP),
    floor=(CheckKind.SCHEMA,),
    max_diff_bytes=1_048_576,
    judge=False,
    audit_rate=0.0,
)
_TIERS = TierTable(tiers={RiskTier.SCRATCH_WRITE: _UNSAMPLED})


def _gate(tmp_path: Path, rig: GuardBeeRig) -> tuple[AuditingCappingGate, RepeatingJudgeReviewer]:
    """A Hive Stand Warden's auditing gate, recording to (and reading) the Guard Bee's trail."""
    deps = GateDeps(
        session=FakeSession(tmp_path / "scratch", rig.clock),
        snapshotter=NoopSnapshotter(),
        cell=make_cell(kind=CellKind.REAL, clock=rig.clock),
        tiers=_TIERS,
        trail=rig.trail,
        identity=rig.identity,
        clock=rig.clock,
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


async def _run_one(gate: AuditingCappingGate, tmp_path: Path) -> None:
    """Propose and run one SCRATCH_WRITE proposal to its end."""
    proposal_id = await gate.propose(make_proposal(risk_tier=RiskTier.SCRATCH_WRITE))
    await gate.run(proposal_id, CapabilitySet.parse(), FakeLeaseView(tmp_path / "scratch"))


async def _failing_audits(rig: GuardBeeRig) -> None:
    """Seed one REJECT among four sampled audits of SCRATCH_WRITE: audit_failure_rate fires."""
    episode = await seed_episode(rig.seed)
    for outcome in ("REJECT", "APPROVE", "APPROVE", "APPROVE"):
        proposal = await rig.seed.proposed(episode.task, episode.cell, "SCRATCH_WRITE")
        await rig.seed.capping(proposal, "capping.audited", tier="SCRATCH_WRITE", outcome=outcome)


async def test_the_guard_bees_raise_is_sampled_by_a_real_gate_on_its_trail(tmp_path: Path) -> None:
    guard = GuardSection(bee=GuardBeeSection(audit_raise_step=1.0))
    rig = make_guard_bee(RigOptions(guard=guard, tiers=_TIERS))
    gate, reviewer = _gate(tmp_path, rig)
    await _run_one(gate, tmp_path)
    unsampled = len(reviewer.calls)
    await _failing_audits(rig)

    [response] = await rig.bee.tick()
    await _run_one(gate, tmp_path)

    assert unsampled == 0  # The table's own rate samples nothing.
    assert response.disposition is Disposition.RAISED
    assert len(reviewer.calls) == 1  # Raised to 1.0: the very next proposal is audited.
