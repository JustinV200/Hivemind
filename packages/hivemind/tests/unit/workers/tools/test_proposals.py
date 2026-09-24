"""Unit tests for hivemind.workers.tools.proposals: make_proposal, cap, describe, tool_output."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from builders.capping import make_action, make_postcondition
from builders.workers import make_assignment, make_context, make_gui_context, run_tool

from hivemind.exoskeleton import Peripherals, ScreenSize
from hivemind.exoskeleton.antennae import FakeAntennae
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.guard import CapabilitySet
from hivemind.supervision.capping import (
    CappingGate,
    GateDeps,
    GateOutcome,
    JudgeOutcome,
    JudgeVerdict,
    LeaseView,
    ProposalState,
    RiskTier,
)
from hivemind.supervision.capping.leave import Asker
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.proposals import (
    MAX_REVIEW_CHARS,
    REVIEW_REJECTED_REASON,
    ProposalRequest,
    cap,
    describe,
    make_proposal,
    tool_output,
)
from waggle.clock import FakeClock
from waggle.ids import MessageId
from waggle.messages import AlarmSeverity
from waggle.messages.labels import PostconditionKind
from waggle.messages.supervision import AlarmKind

_SIZE = ScreenSize(100, 100)


def _desk(
    gate: Callable[[GateDeps], CappingGate] = CappingGate, recording_id: str | None = None
) -> WorkerContext:
    """A display nothing ever changes on, driven through `gate`."""
    clock = FakeClock()
    peripherals = Peripherals(
        compound_eye=FakeCompoundEye(FakeScreen(_SIZE), clock), antennae=FakeAntennae(_SIZE)
    )
    return make_gui_context(peripherals, clock, gate=gate, recording_id=recording_id)


def _reviewed(verdict: JudgeVerdict) -> Callable[[GateDeps], CappingGate]:
    """Build a gate that hands back `verdict` as the judge's review, as the Warden's gate does."""

    class _ReviewedGate(CappingGate):
        async def run(
            self,
            proposal_id: MessageId,
            capabilities: CapabilitySet,
            lease: LeaseView,
            asker: Asker | None = None,
        ) -> GateOutcome:
            outcome = await super().run(proposal_id, capabilities, lease, asker)
            return outcome.model_copy(update={"review": verdict})

    return _ReviewedGate


def _verdict(outcome: JudgeOutcome, *reasons: str) -> JudgeVerdict:
    """A judge's verdict on the irreversible tier."""
    return JudgeVerdict(outcome=outcome, reasons=reasons, rubric_id="irreversible")


def test_make_proposal_stamps_every_id_from_ctx_and_assignment() -> None:
    ctx = make_context()
    assignment = make_assignment()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )

    proposal = make_proposal(ctx, assignment, request)

    assert proposal.task_id == assignment.task_id
    assert proposal.cell_id == ctx.cell.id
    assert proposal.proposer == ctx.worker_id
    assert proposal.risk_tier is RiskTier.SCRATCH_WRITE
    assert proposal.spend_estimate_usd == 0.0
    assert proposal.state is ProposalState.PROPOSED


async def test_cap_proposes_then_runs_and_returns_a_terminal_outcome() -> None:
    ctx = make_context()
    assignment = make_assignment()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )
    proposal = make_proposal(ctx, assignment, request)

    outcome = await cap(ctx, proposal)

    assert outcome.state in (
        ProposalState.VERIFIED,
        ProposalState.REJECTED,
        ProposalState.ROLLED_BACK,
    )
    assert outcome.proposal_id == proposal.id


async def test_describe_never_includes_the_action_diff_text() -> None:
    ctx = make_context()
    assignment = make_assignment()
    distinctive_diff_text = "@@ -0,0 +1,1 @@\n+top distinctive line\n"
    action = make_action(diff=distinctive_diff_text, paths=("note.txt",))
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=action,
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )
    proposal = make_proposal(ctx, assignment, request)

    outcome = await cap(ctx, proposal)
    text = describe(outcome)

    assert "top distinctive line" not in text
    assert "state=" in text and "reason=" in text


async def test_cap_alarms_at_once_when_a_gui_action_is_rolled_back() -> None:
    ctx = _desk()

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "expect": {"region": "0,0,10,10"}})

    assert result.text.startswith("state=ROLLED_BACK")
    (alarm,) = ctx.telemetry.take_pending_alarms()
    assert alarm.kind is AlarmKind.POSTCONDITION_FAILED
    assert alarm.detail.startswith("GUI proposal msg_") and "[0] REGION_CHANGED" in alarm.detail


async def test_a_gui_alarm_names_the_recording_that_holds_its_evidence() -> None:
    ctx = _desk(recording_id="rec_42")

    await run_tool(ctx, "click", {"x": 5, "y": 5, "expect": {"region": "0,0,10,10"}})

    (alarm,) = ctx.telemetry.take_pending_alarms()
    assert " in recording rec_42 rolled back" in alarm.detail


async def test_cap_counts_any_other_rollback_toward_the_alarm_threshold() -> None:
    ctx = make_context()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(PostconditionKind.FILE_EXISTS, subject="other.txt"),),
        reason="a write whose postcondition names the wrong file",
    )

    outcome = await cap(ctx, make_proposal(ctx, make_assignment(), request))

    assert outcome.state is ProposalState.ROLLED_BACK
    assert ctx.telemetry.take_pending_alarms() == ()  # One of three, not an Alarm yet.


async def test_a_judges_reject_is_a_failed_result_and_one_critical_alarm() -> None:
    verdict = _verdict(JudgeOutcome.REJECT, "it deleted the account", "nothing asked for that")
    ctx = _desk(_reviewed(verdict), recording_id="rec_7")

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "irreversible": True})

    assert result.is_error
    assert result.text.startswith(
        "judge=REJECT (it deleted the account; nothing asked for that); state=VERIFIED"
    )
    # The one Alarm for it (the gate only recorded the verdict), on the path that ends the attempt.
    (alarm,) = ctx.telemetry.take_pending_alarms()
    assert (alarm.kind, alarm.severity, alarm.reason) == (
        AlarmKind.AUDIT_FAILED,
        AlarmSeverity.CRITICAL,
        REVIEW_REJECTED_REASON,
    )
    assert alarm.detail.endswith(
        "in recording rec_7 was rejected on review: it deleted the account; nothing asked for that"
    )


@pytest.mark.parametrize("outcome", [JudgeOutcome.APPROVE, JudgeOutcome.REQUEST_CHANGES])
async def test_any_other_verdict_is_one_note_on_a_successful_result(outcome: JudgeOutcome) -> None:
    ctx = _desk(_reviewed(_verdict(outcome, "fine")))

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "irreversible": True})

    assert not result.is_error
    assert result.text.startswith("state=VERIFIED")
    assert result.text.endswith(f"; judge={outcome.value} (fine)")


async def test_a_judges_reasons_are_cut_to_the_review_bound() -> None:
    ctx = _desk(_reviewed(_verdict(JudgeOutcome.REJECT, "x" * 500, "y" * 500)))

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "irreversible": True})

    reasons = result.text.split("(", 1)[1].split(")", 1)[0]
    assert len(reasons) == MAX_REVIEW_CHARS + len("...")


async def test_tool_output_flags_every_outcome_but_verified_as_an_error() -> None:
    ctx = make_context()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )

    outcome = await cap(ctx, make_proposal(ctx, make_assignment(), request))

    assert outcome.state is ProposalState.VERIFIED
    assert tool_output(outcome).is_error is False
    rejected = outcome.model_copy(update={"state": ProposalState.REJECTED})
    assert tool_output(rejected).is_error is True
