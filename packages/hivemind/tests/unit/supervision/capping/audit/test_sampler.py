"""Unit tests for hivemind.supervision.capping.audit.sampler: AuditSampler, audit_completed, rates.

Also review_applied, the unsampled review of an applied irreversible GUI proposal (ADR-0032).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from builders.capping import make_judge_rubric, make_judge_verdict, make_proposal
from builders.cells import make_identity

from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.supervision.capping.audit.sampler import (
    AuditDeps,
    AuditRates,
    AuditSampler,
    InMemoryFindingsSink,
    audit_completed,
    review_applied,
)
from hivemind.supervision.capping.checks.fake import FakeJudgeReviewer
from hivemind.supervision.capping.checks.judge import JudgeEvidence, JudgeOutcome, JudgeVerdict
from hivemind.supervision.capping.errors import (
    CappingError,
    JudgeAnswerError,
    JudgeUnavailableError,
)
from hivemind.supervision.capping.tiers import RiskTier, TierSpec
from waggle.clock import FakeClock

# ──────────────────────────────────────────────────────────────────────────────
# AuditSampler
# ──────────────────────────────────────────────────────────────────────────────


def test_audit_sampler_never_samples_a_zero_rate() -> None:
    sampler = AuditSampler()
    proposal = make_proposal()

    assert sampler.should_sample(proposal.id, RiskTier.SCRATCH_WRITE, 0.0) is False


def test_audit_sampler_always_samples_a_rate_of_one() -> None:
    sampler = AuditSampler()
    proposal = make_proposal()

    assert sampler.should_sample(proposal.id, RiskTier.SCRATCH_WRITE, 1.0) is True


def test_audit_sampler_deterministic_default_agrees_with_itself_across_calls() -> None:
    # No injected Random: the same proposal id and tier must land on the same decision every
    # time, so a test asserting sampling never needs a retry loop.
    sampler = AuditSampler()
    proposal = make_proposal()

    first = sampler.should_sample(proposal.id, RiskTier.SPEND, 0.5)
    second = sampler.should_sample(proposal.id, RiskTier.SPEND, 0.5)

    assert first == second


def test_audit_sampler_injected_random_is_used_instead_of_the_hash() -> None:
    class _AlwaysZero:
        def random(self) -> float:
            return 0.0

    sampler = AuditSampler(rng=_AlwaysZero())  # type: ignore[arg-type]
    proposal = make_proposal()

    # 0.0 < any positive rate, so an injected rng that always returns 0.0 always samples.
    assert sampler.should_sample(proposal.id, RiskTier.SCRATCH_WRITE, 0.01) is True


# ──────────────────────────────────────────────────────────────────────────────
# AuditRates
# ──────────────────────────────────────────────────────────────────────────────


def test_audit_rates_starts_at_zero_for_an_unsampled_tier() -> None:
    rates = AuditRates()

    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 0
    assert rates.failed(RiskTier.SCRATCH_WRITE) == 0
    assert rates.failure_rate(RiskTier.SCRATCH_WRITE) == 0.0


def test_audit_rates_records_samples_and_failures_per_tier() -> None:
    rates = AuditRates()

    rates.record_sample(RiskTier.SCRATCH_WRITE, failed=False)
    rates.record_sample(RiskTier.SCRATCH_WRITE, failed=True)
    rates.record_sample(RiskTier.SPEND, failed=True)

    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 2
    assert rates.failed(RiskTier.SCRATCH_WRITE) == 1
    assert rates.failure_rate(RiskTier.SCRATCH_WRITE) == 0.5
    assert rates.sampled(RiskTier.SPEND) == 1
    assert rates.failure_rate(RiskTier.SPEND) == 1.0


# ──────────────────────────────────────────────────────────────────────────────
# audit_completed
# ──────────────────────────────────────────────────────────────────────────────


def _deps(
    reviewer: FakeJudgeReviewer, *, sample_always: bool = True
) -> tuple[AuditDeps, MemoryPheromoneTrail]:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    rng = _AlwaysSample() if sample_always else _NeverSample()
    deps = AuditDeps(
        sampler=AuditSampler(rng=rng),  # type: ignore[arg-type]
        reviewer=reviewer,
        rubrics={RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)},
        sink=InMemoryFindingsSink(),
        trail=trail,
        identity=make_identity(clock),
        clock=clock,
    )
    return deps, trail


class _AlwaysSample:
    """A stand-in random.Random that always samples: `.random()` returns 0.0."""

    def random(self) -> float:
        return 0.0


class _NeverSample:
    """A stand-in random.Random that never samples: `.random()` returns just under 1.0."""

    def random(self) -> float:
        return 0.999_999


async def test_audit_completed_returns_none_when_not_sampled() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict())
    deps, _trail = _deps(reviewer, sample_always=False)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    tier = TierSpec(checks=(), floor=(), audit_rate=0.5)
    rates = AuditRates()

    verdict = await audit_completed(deps, proposal, tier, rates)

    assert verdict is None
    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 0
    assert reviewer.calls == []


async def test_audit_completed_reviews_and_records_a_sampled_approve() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict(JudgeOutcome.APPROVE))
    deps, trail = _deps(reviewer)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)
    rates = AuditRates()

    verdict = await audit_completed(deps, proposal, tier, rates)

    assert verdict is not None
    assert verdict.outcome is JudgeOutcome.APPROVE
    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 1
    assert rates.failed(RiskTier.SCRATCH_WRITE) == 0
    events = await trail.query(TrailQuery(subject_id=proposal.id))
    kinds = [event.kind for event in events]
    assert "capping.audited" in kinds
    assert "alarm.raised" not in kinds  # APPROVE never raises an Alarm.


async def test_audit_completed_deposits_a_finding_for_every_sampled_proposal() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict(JudgeOutcome.APPROVE))
    deps, _trail = _deps(reviewer)
    sink = deps.sink
    assert isinstance(sink, InMemoryFindingsSink)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)

    await audit_completed(deps, proposal, tier, AuditRates())

    assert len(sink.findings) == 1
    assert sink.findings[0].proposal_id == proposal.id


async def test_audit_completed_raises_an_alarm_on_reject() -> None:
    reviewer = FakeJudgeReviewer(
        make_judge_verdict(JudgeOutcome.REJECT, reasons=("Out of scope.",))
    )
    deps, trail = _deps(reviewer)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)
    rates = AuditRates()

    verdict = await audit_completed(deps, proposal, tier, rates)

    assert verdict is not None
    assert verdict.outcome is JudgeOutcome.REJECT
    assert rates.failed(RiskTier.SCRATCH_WRITE) == 1
    # record_alarm_event stamps subject_id with the Alarm's own id, not the proposal's (alarm_trail
    # docstring), so an alarm.raised step is found by kind/family, not by the proposal's subject.
    events = await trail.query(TrailQuery(family="alarm"))
    alarm_events = [event for event in events if event.kind == "alarm.raised"]
    assert len(alarm_events) == 1
    assert alarm_events[0].payload["kind"] == "AUDIT_FAILED"


async def test_audit_completed_payload_carries_no_reasons_text() -> None:
    reviewer = FakeJudgeReviewer(
        make_judge_verdict(JudgeOutcome.REJECT, reasons=("Secret detail nobody put on the trail.",))
    )
    deps, trail = _deps(reviewer)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)

    await audit_completed(deps, proposal, tier, AuditRates())

    events = await trail.query(TrailQuery(subject_id=proposal.id))
    audited = next(event for event in events if event.kind == "capping.audited")
    assert set(audited.payload) == {"tier", "outcome", "rubric_id"}


async def test_audit_completed_raises_capping_error_for_an_unconfigured_rubric() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict())
    deps, _trail = _deps(reviewer)
    proposal = make_proposal(risk_tier=RiskTier.SPEND)  # No SPEND rubric in deps.rubrics.
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)

    with pytest.raises(CappingError, match="No judge rubric configured"):
        await audit_completed(deps, proposal, tier, AuditRates())


class _SilentReviewer:
    """A JudgeReviewer whose model never produces a verdict, as a dry ladder raises."""

    async def review(self, request: object) -> JudgeVerdict:
        raise JudgeAnswerError("Provider 'fake' produced unparseable output after 4 attempt(s).")


async def test_audit_completed_records_an_inconclusive_sample_when_the_judge_cannot_answer() -> (
    None
):
    # A 2 % scratch_write audit against a provider with no judge answer used to propagate out of
    # the gate and crash the Drone mid-task (the compose-test flake of 2026-09-22).
    deps, trail = _deps(_SilentReviewer())  # type: ignore[arg-type]
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)
    rates = AuditRates()

    verdict = await audit_completed(deps, proposal, tier, rates)

    assert verdict is None
    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 0  # Inconclusive: neither passed nor failed.
    events = await trail.query(TrailQuery(subject_id=proposal.id))
    audited = [event for event in events if event.kind == "capping.audited"]
    assert len(audited) == 1
    assert audited[0].payload["judge_error"] is True
    assert audited[0].payload["outcome"] is None
    assert "unparseable" not in str(audited[0].payload)  # The error text stays off the trail.
    alarms = await trail.query(TrailQuery(family="alarm"))
    assert alarms == ()


async def test_audit_completed_records_an_inconclusive_sample_for_an_unscripted_reviewer() -> None:
    # WardenDeps.judge_reviewer defaults to a bare FakeJudgeReviewer() (wardens/deps.py): a
    # Virtual Cell's Warden whose slot table binds no judge has no ModelJudgeReviewer wired, so any
    # sampled proposal there hits this, not _SilentReviewer's JudgeAnswerError -- found on a real
    # Docker Virtual Cell run (2026-09-24), where it crashed the Drone the same way the 2026-09-22
    # flake did; a raised audit rate makes every one of its samples reach it (roadmap step 10.6).
    deps, trail = _deps(FakeJudgeReviewer())
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)
    rates = AuditRates()

    verdict = await audit_completed(deps, proposal, tier, rates)

    assert verdict is None
    assert rates.sampled(RiskTier.SCRATCH_WRITE) == 0  # Inconclusive: neither passed nor failed.
    events = await trail.query(TrailQuery(subject_id=proposal.id))
    audited = [event for event in events if event.kind == "capping.audited"]
    assert len(audited) == 1
    assert (audited[0].payload["outcome"], audited[0].payload["judge_error"]) == (None, True)
    alarms = await trail.query(TrailQuery(family="alarm"))
    assert alarms == ()
    assert JudgeUnavailableError.code == "hivemind.supervision.capping.judge_unavailable"


async def test_audit_completed_hands_recorded_evidence_to_the_judge() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict())
    deps, _trail = _deps(reviewer)
    evidence = JudgeEvidence(text="After: url=file:///site/welcome.html", frames=(b"png",))
    tier = TierSpec(checks=(), floor=(), audit_rate=1.0)

    await audit_completed(deps, make_proposal(), tier, AuditRates(), evidence)

    assert reviewer.calls[0].evidence == evidence


# ──────────────────────────────────────────────────────────────────────────────
# review_applied
# ──────────────────────────────────────────────────────────────────────────────


def _review_deps(reviewer: object) -> tuple[AuditDeps, MemoryPheromoneTrail]:
    """AuditDeps that never sample, with an irreversible rubric: review_applied must not care."""
    deps, trail = _deps(reviewer, sample_always=False)  # type: ignore[arg-type]
    rubrics = {RiskTier.IRREVERSIBLE: make_judge_rubric(RiskTier.IRREVERSIBLE)}
    return replace(deps, rubrics=rubrics), trail


async def test_review_applied_judges_without_sampling_and_records_the_verdict() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict(JudgeOutcome.APPROVE))
    deps, trail = _review_deps(reviewer)
    proposal = make_proposal(risk_tier=RiskTier.IRREVERSIBLE)
    evidence = JudgeEvidence(text="Steps:\n- click role=button name='Pay'")

    verdict = await review_applied(deps, proposal, evidence)

    assert verdict.outcome is JudgeOutcome.APPROVE
    assert reviewer.calls[0].evidence == evidence
    assert reviewer.calls[0].risk_tier is RiskTier.IRREVERSIBLE
    sink = deps.sink
    assert isinstance(sink, InMemoryFindingsSink)
    assert [finding.proposal_id for finding in sink.findings] == [proposal.id]
    kinds = [event.kind for event in await trail.query(TrailQuery())]
    assert "capping.audited" in kinds
    assert "alarm.raised" not in kinds


async def test_review_applied_records_a_reject_and_leaves_the_alarm_to_the_proposer() -> None:
    reviewer = FakeJudgeReviewer(
        make_judge_verdict(JudgeOutcome.REJECT, reasons=("Paid the wrong invoice.",))
    )
    deps, trail = _review_deps(reviewer)
    proposal = make_proposal(risk_tier=RiskTier.IRREVERSIBLE)

    verdict = await review_applied(deps, proposal, None)

    assert verdict.outcome is JudgeOutcome.REJECT
    (audited,) = await trail.query(TrailQuery(subject_id=proposal.id))
    assert audited.payload["outcome"] == "REJECT"
    # The Worker's tool raises the one Alarm, on the path whose escalation ends the attempt.
    assert await trail.query(TrailQuery(family="alarm")) == ()


async def test_review_applied_fails_closed_when_the_judge_cannot_answer() -> None:
    deps, trail = _review_deps(_SilentReviewer())

    verdict = await review_applied(deps, make_proposal(risk_tier=RiskTier.IRREVERSIBLE), None)

    # Unlike a sampled audit, nobody vouched for an action that cannot be undone: a REJECT.
    assert verdict.outcome is JudgeOutcome.REJECT
    assert verdict.reasons[0].startswith("the judge could not answer")
    assert verdict.rubric_id == "irreversible-test"
    assert [event.payload["outcome"] for event in await trail.query(TrailQuery())] == ["REJECT"]


async def test_review_applied_fails_closed_for_an_unscripted_reviewer() -> None:
    # Same WardenDeps default gap as test_audit_completed_records_an_inconclusive_sample_for_an_
    # unscripted_reviewer, but on the never-sampled, always-reviewed irreversible path: it must
    # fail closed (a REJECT), not propagate JudgeUnavailableError and crash the Drone.
    deps, trail = _review_deps(FakeJudgeReviewer())

    verdict = await review_applied(deps, make_proposal(risk_tier=RiskTier.IRREVERSIBLE), None)

    assert verdict.outcome is JudgeOutcome.REJECT
    assert verdict.reasons[0].startswith("the judge could not answer")
    assert verdict.rubric_id == "irreversible-test"
    assert [event.payload["outcome"] for event in await trail.query(TrailQuery())] == ["REJECT"]
