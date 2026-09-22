"""Unit tests for hivemind.supervision.capping.checks.judge: JudgeCheck, JudgeVerdict, etc."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.capping import (
    FakeLeaseView,
    make_judge_rubric,
    make_judge_verdict,
    make_postcondition,
    make_proposal,
    make_tier_table,
)
from pydantic import ValidationError

from hivemind.guard import CapabilitySet
from hivemind.supervision.capping.checks.base import CheckContext
from hivemind.supervision.capping.checks.fake import FakeJudgeReviewer
from hivemind.supervision.capping.checks.judge import (
    JudgeCheck,
    JudgeOutcome,
    JudgeRequest,
    judge_checks,
)
from hivemind.supervision.capping.errors import JudgeAnswerError
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import CheckKind, CheckOutcome


def _context(scratch_root: Path) -> CheckContext:
    """Build a CheckContext for a SCRATCH_WRITE proposal, the shape JudgeCheck.run expects."""
    tier = make_tier_table().tiers[RiskTier.SCRATCH_WRITE]
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    return CheckContext(
        proposal=proposal,
        capabilities=CapabilitySet.parse(),
        lease=FakeLeaseView(scratch_root),
        scratch_root=scratch_root,
        tier=tier,
    )


# ──────────────────────────────────────────────────────────────────────────────
# JudgeVerdict / JudgeRequest: boundary models
# ──────────────────────────────────────────────────────────────────────────────


def test_judge_verdict_round_trips_through_json() -> None:
    verdict = make_judge_verdict()

    restored = verdict.model_validate_json(verdict.model_dump_json())

    assert restored == verdict


def test_judge_verdict_is_frozen_and_forbids_extras() -> None:
    verdict = make_judge_verdict()

    with pytest.raises(ValidationError, match="frozen"):
        verdict.notes = "nope"  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError):
        verdict.model_validate({**verdict.model_dump(), "extra": "nope"})


def test_judge_request_carries_no_proposer_or_transcript_field() -> None:
    # Codingrules 8.12: "no shared context with the proposing bee." JudgeRequest's field set is
    # the whole enforcement of that rule -- assert it directly, so a later field addition that
    # smuggles in a proposer id or transcript fails this test. `tempo` is the task's own
    # speed-against-accuracy setting (it orders the judge's call in the Fanner's queues), never
    # anything the proposing bee thought or did.
    assert set(JudgeRequest.model_fields) == {
        "risk_tier",
        "action",
        "acceptance_criteria",
        "rubric",
        "tempo",
    }


# ──────────────────────────────────────────────────────────────────────────────
# JudgeCheck.run
# ──────────────────────────────────────────────────────────────────────────────


async def test_judge_check_run_passes_on_approve(tmp_path: Path) -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict(JudgeOutcome.APPROVE))
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}
    check = JudgeCheck(reviewer, rubrics)

    result = await check.run(_context(tmp_path))

    assert result.kind is CheckKind.JUDGE
    assert result.outcome is CheckOutcome.PASSED


async def test_judge_check_run_reports_changes_requested(tmp_path: Path) -> None:
    reviewer = FakeJudgeReviewer(
        make_judge_verdict(JudgeOutcome.REQUEST_CHANGES, reasons=("Diff exceeds its summary.",))
    )
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}
    check = JudgeCheck(reviewer, rubrics)

    result = await check.run(_context(tmp_path))

    assert result.outcome is CheckOutcome.CHANGES_REQUESTED
    assert "exceeds its summary" in result.reason


async def test_judge_check_run_fails_on_reject(tmp_path: Path) -> None:
    reviewer = FakeJudgeReviewer(
        make_judge_verdict(JudgeOutcome.REJECT, reasons=("Out of scope.",))
    )
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}
    check = JudgeCheck(reviewer, rubrics)

    result = await check.run(_context(tmp_path))

    assert result.outcome is CheckOutcome.FAILED


class _RaisingJudgeReviewer:
    """A JudgeReviewer that always raises JudgeAnswerError instead of answering (test-only)."""

    def __init__(self, detail: str) -> None:
        self._detail = detail

    async def review(self, request: JudgeRequest) -> object:
        raise JudgeAnswerError(self._detail)


async def test_judge_check_run_fails_closed_when_the_reviewer_cannot_answer(
    tmp_path: Path,
) -> None:
    """A judge that cannot produce a verdict is a check outcome, not a bee crash.

    2026-09-21: JudgeCheck.run must catch JudgeAnswerError and report FAILED with
    judge_error=True, never let it propagate (the real trail: nine unparseable llm.call attempts
    reached worker.failed).
    """
    reviewer = _RaisingJudgeReviewer("unparseable output after 9 attempt(s): ''")
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}
    check = JudgeCheck(reviewer, rubrics)  # type: ignore[arg-type]

    result = await check.run(_context(tmp_path))

    assert result.outcome is CheckOutcome.FAILED
    assert result.judge_error is True
    assert "judge could not produce a verdict" in result.reason
    assert "unparseable output after 9 attempt(s)" in result.reason


async def test_judge_check_run_fails_closed_with_no_rubric_for_tier(tmp_path: Path) -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict())
    check = JudgeCheck(reviewer, rubrics={})  # No SCRATCH_WRITE rubric configured.

    result = await check.run(_context(tmp_path))

    assert result.outcome is CheckOutcome.FAILED
    assert result.reason == "no rubric configured for tier"
    assert reviewer.calls == []  # Never reached the reviewer: the rubric lookup failed first.


async def test_judge_check_run_sends_no_proposer_or_transcript_to_the_reviewer(
    tmp_path: Path,
) -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict())
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}
    check = JudgeCheck(reviewer, rubrics)
    context = _context(tmp_path)

    await check.run(context)

    sent = reviewer.calls[0]
    assert sent.risk_tier is context.proposal.risk_tier
    assert sent.action == context.proposal.action
    assert sent.acceptance_criteria == context.proposal.postconditions
    assert not hasattr(sent, "proposer")


async def test_judge_check_run_uses_the_acceptance_criteria_from_the_proposal(
    tmp_path: Path,
) -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict())
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}
    check = JudgeCheck(reviewer, rubrics)
    pc = make_postcondition()
    tier = make_tier_table().tiers[RiskTier.SCRATCH_WRITE]
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE, postconditions=(pc,))
    context = CheckContext(
        proposal=proposal,
        capabilities=CapabilitySet.parse(),
        lease=FakeLeaseView(tmp_path),
        scratch_root=tmp_path,
        tier=tier,
    )

    await check.run(context)

    assert reviewer.calls[0].acceptance_criteria == (pc,)


# ──────────────────────────────────────────────────────────────────────────────
# judge_checks: deterministic_checks()'s sibling registry
# ──────────────────────────────────────────────────────────────────────────────


def test_judge_checks_builds_a_one_entry_registry_under_check_kind_judge() -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict())
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}

    registry = judge_checks(reviewer, rubrics)

    assert set(registry) == {CheckKind.JUDGE}
    assert isinstance(registry[CheckKind.JUDGE], JudgeCheck)


async def test_judge_checks_registry_entry_actually_calls_the_reviewer(tmp_path: Path) -> None:
    reviewer = FakeJudgeReviewer(make_judge_verdict(JudgeOutcome.APPROVE))
    rubrics = {RiskTier.SCRATCH_WRITE: make_judge_rubric(RiskTier.SCRATCH_WRITE)}
    registry = judge_checks(reviewer, rubrics)

    result = await registry[CheckKind.JUDGE].run(_context(tmp_path))

    assert result.outcome is CheckOutcome.PASSED
    assert len(reviewer.calls) == 1
